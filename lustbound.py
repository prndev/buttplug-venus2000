import asyncio
import websockets
import json
import time
import collections
from serial import Serial
import argparse
import collections

def clamp(value):
    return min(1.0, max(0.0, value))

Sample = collections.namedtuple('Sample', ['value', 'timestamp_ms'])

class VibrationHandler:
    def __init__(self, args, set_intensity_callback):
        self.serial_read_timeout = None
        self.samples:collections.deque[Sample] = collections.deque()
        self.cycle_times_ms:collections.deque[int] = collections.deque()
        self.falling_timestamp_ms = None
        self.rising_timestamp_ms = None
        self.set_intensity_callback = set_intensity_callback
        self.args = args

    def stop(self):
        self.serial_read_timeout = None
        self.samples.clear()
        self.cycle_times_ms.clear()
        self.falling_timestamp_ms = None
        self.rising_timestamp_ms = None
        self.set_intensity_callback(0.0)

    def update(self, value, timestamp_ms):
        self.serial_read_timeout = 0.1 # we are no longer "stopped by timeout"
        sample = Sample(value, timestamp_ms)
        # calculate average cycle time
        avg_cycle_time = None
        if (len(self.samples)):
            if (self.samples[-1].value > value): # falling slope
                if (self.falling_timestamp_ms and self.rising_timestamp_ms):
                    if (self.falling_timestamp_ms < self.rising_timestamp_ms):
                        self.cycle_times_ms.append(self.rising_timestamp_ms-self.falling_timestamp_ms)
                self.falling_timestamp_ms = timestamp_ms
            if (self.samples[-1].value < value):
                if (self.falling_timestamp_ms and self.rising_timestamp_ms):
                    if (self.rising_timestamp_ms < self.falling_timestamp_ms):
                        self.cycle_times_ms.append(self.falling_timestamp_ms-self.rising_timestamp_ms)
                self.rising_timestamp_ms = timestamp_ms
            # limit amount of (half-)cycles to averare over
            while(len(self.cycle_times_ms) > args.cycle_max_samples):
                self.cycle_times_ms.popleft()
            if (self.cycle_times_ms):
                avg_cycle_time = sum(self.cycle_times_ms)/len(self.cycle_times_ms)
        self.samples.append(sample)
        
        # remove old samples
        while (self.samples[-1].timestamp_ms-self.samples[0].timestamp_ms > args.amplitude_sample_ms):
            self.samples.popleft()
        # get amplitude daftly
        amplitude = max(self.samples).value - min(self.samples).value
        
        cycle_time = avg_cycle_time if avg_cycle_time else self.args.cycle_max_ms
        cycle_intensity = clamp((self.args.cycle_max_ms+self.args.cycle_min_ms - cycle_time)/(self.args.cycle_max_ms-self.args.cycle_min_ms))
        intensity = amplitude * cycle_intensity
        set_intensity(intensity)

        print(f"{amplitude:.2} {cycle_intensity:.2} → {intensity:.2}")

async def handle_client(websocket, args, set_intensity):
    print("Client connected!")
    try:
        message_version = 2
        client_connect_timestamp_ms = time.time_ns() // 1_000_000
        vibration_handler = VibrationHandler(args, set_intensity)
        while True:
            message = None
            #async for message in websocket:
            try:
                async with asyncio.timeout(vibration_handler.serial_read_timeout):
                    message = await websocket.recv()
            except TimeoutError:
                print("Stop by timeout")
                vibration_handler.stop()
            if (message):
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
                            response = [{"ServerInfo":{"Id":message_id,"MessageVersion":message_version,"MaxPingTime":0,"ServerName":"Intiface Server"}}]
                        else:
                            error_code = 1 #ERROR_INIT
                            response = [{"Error":{"Id":message_id,"ErrorCode":error_code,"ErrorMessage":"This server can only handle a Lustbound client."}}]
                elif ("RequestDeviceList" in message):
                    if (message_version == 2):
                        response = [{"DeviceList":{"Id":message_id,"Devices":[{"DeviceIndex":0,"DeviceName":"Venus2000 (Lustbound Adapter)","DeviceMessages":{"VibrateCmd":{"FeatureCount":1,"StepCount":[180]},"StopDeviceCmd":{}}}]}}]
                elif ("VibrateCmd" in message): # NOTE: only exists in message_version 2
                    value = message["VibrateCmd"]["Speeds"][0]["Speed"]
                    timestamp_ms = (time.time_ns() // 1_000_000) - client_connect_timestamp_ms
                    vibration_handler.update(value, timestamp_ms)
                print(">", response)
                await websocket.send(json.dumps(response))
    except websockets.exceptions.ConnectionClosed:
        pass

# Main function to start the WebSocket server
async def main(args, set_intensity):
    async def handler(websocket):
        await handle_client(websocket, args, set_intensity)
    server = await websockets.serve(handler, "localhost", args.port)
    await server.wait_closed()

# Run the server
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=12345)
    parser.add_argument('--servo_max_degrees', type=int, default=180)
    parser.add_argument('--cycle_max_ms', type=int, default=1500)
    parser.add_argument('--cycle_min_ms', type=int, default=200)
    parser.add_argument('--cycle_max_samples', type=int, default=6)
    parser.add_argument('--amplitude_sample_ms', type=int, default=5000)
    # NOTE: 0.7 and 170 seems to be the game's maximum intensity
    args = parser.parse_args()
    
    serial = Serial('/dev/ttyACM0', 9600, timeout=1)
    def set_intensity(intensity:float):
        angle = int(180 * clamp(1.0-intensity))
        serial.write(bytes([angle]))
    set_intensity(0.0)

    asyncio.run(main(args, set_intensity))
