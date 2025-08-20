import asyncio
import websockets
import json
import time
import collections
from serial import Serial
import argparse
import collections
try:
    import pyqtgraph
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
        if pyqtgraph:
            self.intensities:collections.deque[float] = collections.deque()
            self.cycle_intensities:collections.deque[float] = collections.deque()
            self.plot_app = pyqtgraph.QtWidgets.QApplication([])
            self.win = pyqtgraph.GraphicsLayoutWidget(title='Sample Visualization')
            self.plot = self.win.addPlot()
            self.amplitude_plot = self.plot.plot(pen='yellow')
            self.amplitude_max_line = self.plot.addLine(y=1.0, pen=pyqtgraph.mkPen('yellow', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.amplitude_min_line = self.plot.addLine(y=0.0, pen=pyqtgraph.mkPen('yellow', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.falling_edge_line = self.plot.addLine(x=0, pen=pyqtgraph.mkPen('turquoise', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.rising_edge_line = self.plot.addLine(x=0, pen=pyqtgraph.mkPen('cyan', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.cycle_intensity_plot = self.plot.plot(pen=pyqtgraph.mkPen('green', style=pyqtgraph.QtCore.Qt.PenStyle.DashLine))
            self.intensity_plot = self.plot.plot(pen='red')
            self.win.show()
            self.plot_app.processEvents()

    def stop(self):
        self.websocket_read_timeout = None # wait for next instruction forever
        self.samples.clear()
        self.cycle_times_ms.clear()
        self.falling_timestamp_ms = None
        self.rising_timestamp_ms = None
        self.set_intensity_callback(0.0)

    def update(self, value, timestamp_ms):
        self.websocket_read_timeout = 0.1 # we are no longer "stopped by timeout"
        sample = Sample(value, timestamp_ms)

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
        self.samples.append(sample)
        if not avg_cycle_time:
            # in case we do not have enough data yet, assume the maximum cycle time
            avg_cycle_time = self.args.cycle_max_ms
        
        # remove old samples
        while (self.samples[-1].timestamp_ms-self.samples[0].timestamp_ms > args.amplitude_sample_ms):
            self.samples.popleft()
        # get amplitude daftly
        amplitude = max(self.samples).value - min(self.samples).value
        
        # map cycle time to intensity in a linear fashion
        cycle_intensity = clamp((self.args.cycle_max_ms+self.args.cycle_min_ms - avg_cycle_time)/(self.args.cycle_max_ms-self.args.cycle_min_ms))

        # combine intensities
        intensity = amplitude * cycle_intensity

        # propagate intensity to hardware
        set_intensity(intensity)

        #print(f"{amplitude:.2} {cycle_intensity:.2} → {intensity:.2}")
        if pyqtgraph:
            self.intensities.append(intensity)
            self.cycle_intensities.append(cycle_intensity)
            # in no way this guarantees that timestamps match the intensity, but at least we have something to look at
            while(len(self.intensities) > len(self.samples)):
                self.intensities.popleft()
            while(len(self.cycle_intensities) > len(self.samples)):
                self.cycle_intensities.popleft()
            timestamps = [s.timestamp_ms for s in self.samples]
            values = [s.value for s in self.samples]
            self.amplitude_plot.setData(timestamps, values)
            self.intensity_plot.setData(timestamps, self.intensities)
            self.cycle_intensity_plot.setData(timestamps, self.cycle_intensities)
            self.amplitude_max_line.setValue(max(self.samples).value)
            self.amplitude_min_line.setValue(min(self.samples).value)
            if (self.falling_timestamp_ms):
                self.falling_edge_line.setValue(self.falling_timestamp_ms)
            if (self.rising_timestamp_ms):
                self.rising_edge_line.setValue(self.rising_timestamp_ms)
            self.plot_app.processEvents()

async def handle_client(websocket, args, set_intensity):
    try:
        message_version = 2
        client_connect_timestamp_ms = time.time_ns() // 1_000_000
        vibration_handler = VibrationHandler(args, set_intensity)
        while True:
            message = None
            #async for message in websocket:
            try:
                async with asyncio.timeout(vibration_handler.websocket_read_timeout):
                    message = await websocket.recv()
            except TimeoutError:
                #print("Stop by timeout")
                vibration_handler.stop()
            if (message):
                message = json.loads(message)
                message = message[0]
                #print("<", message)
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
                elif ("VibrateCmd" in message): # NOTE: only exists in message_version 2
                    value = message["VibrateCmd"]["Speeds"][0]["Speed"]
                    timestamp_ms = (time.time_ns() // 1_000_000) - client_connect_timestamp_ms
                    vibration_handler.update(value, timestamp_ms)
                #print(">", response)
                await websocket.send(json.dumps(response))
    except websockets.exceptions.ConnectionClosed:
        pass

async def main(args, set_intensity):
    async def handler(websocket):
        await handle_client(websocket, args, set_intensity)
    server = await websockets.serve(handler, "localhost", args.port)
    await server.wait_closed()

# Run the server
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', type=str, default='/dev/ttyACM0')
    parser.add_argument('--baud', type=int, default=9600)
    parser.add_argument('--port', type=int, default=12345)
    parser.add_argument('--servo_max_degrees', type=int, default=180)
    parser.add_argument('--cycle_max_ms', type=int, default=1500)
    parser.add_argument('--cycle_min_ms', type=int, default=200)
    parser.add_argument('--cycle_max_samples', type=int, default=6)
    parser.add_argument('--amplitude_sample_ms', type=int, default=5000)
    # NOTE: amplitude 0.7 and cycle time 170 seems to be around the game's maximum intensity
    args = parser.parse_args()
    
    # open serial connection to hardware
    serial = Serial(args.serial, args.baud, timeout=1)

    # prepare an intensity handler to pass on to the server
    def set_intensity(intensity:float):
        angle = int(args.servo_max_degrees * clamp(1.0-intensity)) # inversion due to gears being gears
        serial.write(bytes([angle]))
    set_intensity(0.0)

    # now run the server
    asyncio.run(main(args, set_intensity))
