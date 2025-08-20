import asyncio
import websockets
import json
import time
import collections
import serial

ser = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
ser.write(bytes([0]))

async def handle_client(websocket):
    print("Client connected!")
    try:
        message_version = 2
        client_connect_timestamp_ms = time.time_ns() // 1_000_000
        timeout = None
        values = collections.deque()
        cycle_times_ms = collections.deque()
        falling_timestamp_ms = None
        rising_timestamp_ms = None
        while True:
            message = None
            #async for message in websocket:
            try:
                async with asyncio.timeout(timeout):
                    message = await websocket.recv()
            except TimeoutError:
                print("Stop by timeout")
                timeout = None
                values.clear()
                cycle_times_ms.clear()
                falling_timestamp_ms = None
                rising_timestamp_ms = None
                ser.write(bytes([0]))
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
                    #print(timestamp_ms, value)
                    timeout = 0.1
                    if (value > 0.001):
                        avg_cycle_time = None
                        if (len(values)):
                            if (values[-1][0] > value):
                                if (falling_timestamp_ms and rising_timestamp_ms):
                                    if (falling_timestamp_ms < rising_timestamp_ms):
                                        cycle_times_ms.append(rising_timestamp_ms-falling_timestamp_ms)
                                falling_timestamp_ms = timestamp_ms
                            if (values[-1][0] < value):
                                if (falling_timestamp_ms and rising_timestamp_ms):
                                    if (rising_timestamp_ms < falling_timestamp_ms):
                                        cycle_times_ms.append(falling_timestamp_ms-rising_timestamp_ms)
                                rising_timestamp_ms = timestamp_ms
                            while(len(cycle_times_ms) > 10):
                                cycle_times_ms.popleft()
                            if (cycle_times_ms):
                                avg_cycle_time = sum(cycle_times_ms)/len(cycle_times_ms)
                        values.append((value, timestamp_ms))
                        
                        while (values[-1][1]-values[0][1] > 5000):
                            values.popleft()
                        amplitude = max(values)[0]-min(values)[0]
                        
                        max_expected_cycle_time = 1500
                        cycle_time = min(max_expected_cycle_time, avg_cycle_time) if avg_cycle_time else max_expected_cycle_time
                        # NOTE: 0.7 and 170 seems to be the game's maximum intensity
                        angle = int(60 * amplitude * max_expected_cycle_time/cycle_time)
                        print(f"{amplitude:.2} {cycle_time: 4} → {angle: 3}")
                        ser.write(bytes([min(180, max(0, angle))]))
                #elif ("RotateCmd" in message):
                #    print("Got rotate:", message["VibrateCmd"]["Speeds"][0]["Speed"])
                print(">", response)
                await websocket.send(json.dumps(response))
    except websockets.exceptions.ConnectionClosed:
        pass

# Main function to start the WebSocket server
async def main():
    server = await websockets.serve(handle_client, "localhost", 12345)
    await server.wait_closed()

# Run the server
if __name__ == "__main__":
    
    
    asyncio.run(main())
