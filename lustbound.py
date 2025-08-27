import asyncio
import websockets
import websockets.asyncio.client
import json
import time
import collections
from serial import Serial
import argparse
import collections
try:
    # if pyqtgraph is not available or not exactly the version this was tested with, just ditch the visualisation
    import pyqtgraph
    if (pyqtgraph.__version__ != '0.13.4'):
        pyqtgraph = None
except:
    pyqtgraph = None

def clamp(value):
    return min(1.0, max(0.0, value))

Sample = collections.namedtuple('Sample', ['value', 'timestamp_ms'])

class VibrationHandler:
    def __init__(self, args, set_intensity_callback):
        self.websocket_read_timeout = None
        self.samples:collections.deque[Sample] = collections.deque()
        self.cycle_times_ms:collections.deque[int] = collections.deque()
        self.falling_timestamp_ms = None
        self.rising_timestamp_ms = None
        self.set_intensity_callback = set_intensity_callback
        self.args = args
        self.intensity = 0.0
        if pyqtgraph:
            self.intensities:collections.deque[float] = collections.deque()
            self.smoothed_intensities:collections.deque[float] = collections.deque()
            self.cycle_intensities:collections.deque[float] = collections.deque()
            self.plot_app = pyqtgraph.QtWidgets.QApplication([])
            self.win = pyqtgraph.GraphicsLayoutWidget(title='Sample Visualization')
            self.plot = self.win.addPlot()
            self.plot.setYRange(0.0, 1.0)
            self.amplitude_plot = self.plot.plot(pen='yellow')
            self.amplitude_max_line = self.plot.addLine(y=1.0, pen=pyqtgraph.mkPen('yellow', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.amplitude_min_line = self.plot.addLine(y=0.0, pen=pyqtgraph.mkPen('yellow', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.falling_edge_line = self.plot.addLine(x=0, pen=pyqtgraph.mkPen('turquoise', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.rising_edge_line = self.plot.addLine(x=0, pen=pyqtgraph.mkPen('cyan', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.cycle_intensity_plot = self.plot.plot(pen=pyqtgraph.mkPen('green', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.intensity_plot = self.plot.plot(pen='red')
            self.smoothed_intensity_plot = self.plot.plot(pen=pyqtgraph.mkPen('red', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.win.show()
            self.plot_app.processEvents()

    def stop(self):
        self.websocket_read_timeout = None # wait for next message forever
        self.samples.clear()
        self.cycle_times_ms.clear()
        self.falling_timestamp_ms = None
        self.rising_timestamp_ms = None
        self.intensity = 0.0
        self.set_intensity_callback(self.intensity)

    def update(self, value, timestamp_ms):
        self.websocket_read_timeout = self.args.minimum_frametime_secs # we are no longer "stopped by timeout" and expect new data within one frame of the game
        sample = Sample(value, timestamp_ms)
        
        # get amplitude daftly
        # we do it before adding the current sample to the history so a potential "zero before animation stop" is not considered
        amplitude = 0.0
        if (self.samples):
            amplitude = max(self.samples).value - min(self.samples).value

        # calculate average cycle time
        avg_cycle_time = None
        if (len(self.samples)):
            # when the animation stops, the game jumps to zero
            # we do not want that to mess up the cycle time calculation
            if (value > 0.0):
                if (self.samples[-1].value > value): # we are on a falling slope
                    if (self.falling_timestamp_ms and self.rising_timestamp_ms):
                        if (self.falling_timestamp_ms < self.rising_timestamp_ms):
                            self.cycle_times_ms.append(self.rising_timestamp_ms-self.falling_timestamp_ms)
                    self.falling_timestamp_ms = timestamp_ms
                if (self.samples[-1].value < value): # we are on a rising slope
                    if (self.falling_timestamp_ms and self.rising_timestamp_ms):
                        if (self.rising_timestamp_ms < self.falling_timestamp_ms):
                            self.cycle_times_ms.append(self.falling_timestamp_ms-self.rising_timestamp_ms)
                    self.rising_timestamp_ms = timestamp_ms
            # limit amount of (half-)cycles to average over
            while(len(self.cycle_times_ms) > args.cycle_max_samples):
                self.cycle_times_ms.popleft()
            if (self.cycle_times_ms):
                avg_cycle_time = sum(self.cycle_times_ms)/len(self.cycle_times_ms)
        if not avg_cycle_time:
            # in case we do not have enough data yet, assume the maximum cycle time
            avg_cycle_time = self.args.cycle_max_ms
        
        self.samples.append(sample)
        
        # remove old samples
        while (self.samples[-1].timestamp_ms-self.samples[0].timestamp_ms > args.amplitude_sample_ms):
            self.samples.popleft()
        
        # map cycle time to intensity in a linear fashion
        cycle_intensity = clamp((self.args.cycle_max_ms+self.args.cycle_min_ms - avg_cycle_time)/(self.args.cycle_max_ms-self.args.cycle_min_ms))

        # combine intensities by averaging
        intensity = (amplitude*self.args.mix + (1.0-self.args.mix)*cycle_intensity)/2
        
        # apply global amplification
        intensity = intensity * self.args.amplification
        
        # smooth changes
        self.intensity = self.intensity*self.args.inertia + intensity*(1.0-self.args.inertia)

        # propagate intensity to hardware
        self.set_intensity_callback(self.intensity)

        #print(f"{amplitude:.2} {cycle_intensity:.2} → {intensity:.2}")
        if pyqtgraph:
            self.intensities.append(intensity)
            self.smoothed_intensities.append(self.intensity)
            self.cycle_intensities.append(cycle_intensity)
            # in no way this guarantees that timestamps match the intensity, but at least we have something to look at
            while(len(self.intensities) > len(self.samples)):
                self.intensities.popleft()
            while(len(self.smoothed_intensities) > len(self.samples)):
                self.smoothed_intensities.popleft()
            while(len(self.cycle_intensities) > len(self.samples)):
                self.cycle_intensities.popleft()
            timestamps = [s.timestamp_ms for s in self.samples]
            values = [s.value for s in self.samples]
            self.amplitude_plot.setData(timestamps, values)
            self.intensity_plot.setData(timestamps, self.intensities)
            self.smoothed_intensity_plot.setData(timestamps, self.smoothed_intensities)
            self.cycle_intensity_plot.setData(timestamps, self.cycle_intensities)
            self.amplitude_max_line.setValue(max(self.samples).value)
            self.amplitude_min_line.setValue(min(self.samples).value)
            if (self.falling_timestamp_ms):
                self.falling_edge_line.setValue(self.falling_timestamp_ms)
            if (self.rising_timestamp_ms):
                self.rising_edge_line.setValue(self.rising_timestamp_ms)
            self.plot_app.processEvents()

async def handle_client(websocket, args, set_intensity, forwarder):
    try:
        message_version = 2
        client_connect_timestamp_ms = time.time_ns() // 1_000_000
        vibration_handler = VibrationHandler(args, set_intensity)
        while True:
            message = None
            try:
                async with asyncio.timeout(vibration_handler.websocket_read_timeout):
                    message = await websocket.recv()
            except TimeoutError:
                #print("Stop by timeout")
                vibration_handler.stop()
            if (message):
                if (forwarder):
                    await forwarder.send(message)
                    print("|", await forwarder.recv())
                message = json.loads(message)
                message = message[0]
                print("<", message)
                message_id = next(iter(message.values()))["Id"]
                response = [{"Ok":{"Id":message_id}}]
                if ("RequestServerInfo" in message):
                    message_version = message["RequestServerInfo"]["MessageVersion"]
                    if (message_version != 2):
                        error_code = 1 #ERROR_INIT
                        response = [{"Error":{"Id":message_id,"ErrorCode":error_code,"ErrorMessage":"This server can only handle a message version 2 client."}}]
                    else:
                        client_name = message["RequestServerInfo"]["ClientName"]
                        if ("Lustbound" in client_name):
                            response = [{"ServerInfo":{"Id":message_id,"MessageVersion":message_version,"MaxPingTime":0,"ServerName":"Lustbound Venus Adapter Server"}}]
                        else:
                            error_code = 1 #ERROR_INIT
                            response = [{"Error":{"Id":message_id,"ErrorCode":error_code,"ErrorMessage":"This server can only handle a Lustbound client."}}]
                elif ("RequestDeviceList" in message):
                    if (message_version == 2):
                        response = [{"DeviceList":{"Id":message_id,"Devices":[{"DeviceIndex":0,"DeviceName":"Venus2000","DeviceMessages":{"VibrateCmd":{"FeatureCount":1,"StepCount":[180]},"StopDeviceCmd":{}}}]}}]
                elif ("VibrateCmd" in message): # NOTE: only exists in message_version <= 2, see https://docs.buttplug.io/docs/spec/deprecated/#vibratecmd
                    value = message["VibrateCmd"]["Speeds"][0]["Speed"]
                    timestamp_ms = (time.time_ns() // 1_000_000) - client_connect_timestamp_ms
                    vibration_handler.update(value, timestamp_ms)
                #print(">", response)
                await websocket.send(json.dumps(response))
    except websockets.exceptions.ConnectionClosed:
        pass

async def main(args, set_intensity, forwarder):
    if (forwarder):
        forwarder = await websockets.asyncio.client.connect(forwarder)
    async def handler(websocket):
        await handle_client(websocket, args, set_intensity, forwarder)
    server = await websockets.serve(handler, "localhost", args.port)
    await server.wait_closed()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Venus2000 Adapter buttplug.io Server')
    parser.add_argument('--serial', type=str, default='/dev/ttyACM0')
    parser.add_argument('--baud', type=int, default=9600)
    parser.add_argument('--port', type=int, default=12345)
    parser.add_argument('--servo_max_degrees', type=int, default=180)
    # NOTE: amplitude 0.7 seems to be around the game's maximum intensity
    parser.add_argument('--mix', type=float, default=0.25, help='Weight of the amplitude when averaging intensities (use 1.0 for "only amplitude" and 0.0 for "only cycle time").')
    parser.add_argument('--amplification', type=float, default=1.25, help="Overall amplification of intensity.")
    parser.add_argument('--inertia', type=float, default=0.9, help='Weight of the history when updating the intensity (use 0.0 for "no inertia, only current value").')
    parser.add_argument('--cycle_max_ms', type=int, default=1500, help="Anything larger than 1500 is a minimal intensity.")
    parser.add_argument('--cycle_min_ms', type=int, default=170, help="170 seems to be the game's maximum intensity (give or take).")
    parser.add_argument('--cycle_max_samples', type=int, default=4)
    parser.add_argument('--amplitude_sample_ms', type=int, default=4000)
    parser.add_argument('--minimum_frametime_secs', type=float, default=0.1)
    parser.add_argument('--forwarder', type=str, help='A websocket URL to copy communication to (e.g. "ws://localhost:12346"). Extremely limited: This will only work with a server capable of handling the obsolete message version 2 format. Only the first toy will receive VibrateCmd messages.')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    
    # open serial connection to hardware
    serial = Serial(args.serial, args.baud, timeout=1)

    # prepare an intensity handler to pass on to the server
    def set_intensity(intensity:float):
        angle = int(args.servo_max_degrees * clamp(1.0 - intensity)) # inversion due to gears being gears
        serial.write(bytes([angle]))
    set_intensity(0.0) # start turned off

    # now run the server
    asyncio.run(main(args, set_intensity, args.forwarder))
