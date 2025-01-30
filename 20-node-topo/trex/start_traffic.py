import sys
import argparse

# adding trex location to the system path
sys.path.insert(0, '/home/cisco/trex/v3.06/trex_client/interactive/')
from trex_stl_lib.api import *

class TRexController:
    def __init__(self):
        # Define all TRex servers and their configurations
        self.servers = {
            'host00': {
                'ip': '172.20.7.200',
                'ua_script': 'host00/host00-uA.py',
                'un_script': 'host00/host00-uN.py'
            },
            'host01': {
                'ip': '172.20.7.201',
                'ua_script': 'host01/host01-uA.py',
                'un_script': 'host01/host01-uN.py'
            },
            'host02': {
                'ip': '172.20.7.202',
                'ua_script': 'host02/host02-uA.py',
                'un_script': 'host02/host02-uN.py'
            },
            'host03': {
                'ip': '172.20.7.203',
                'ua_script': 'host03/host03-uA.py',
                'un_script': 'host03/host03-uN.py'
            }
        }
        self.clients = {}

    def connect_all(self):
        """Connect to all TRex servers"""
        for host, config in self.servers.items():
            try:
                client = STLClient(server=config['ip'])
                client.connect()
                client.reset()
                self.clients[host] = client
                print(f"Connected to {host} at {config['ip']}")
            except STLError as e:
                print(f"Failed to connect to {host}: {e}")

    def start_traffic(self, modes):
        """Start traffic on all servers
        Args:
            modes: list containing 'ua' and/or 'un'
        """
        for host, client in self.clients.items():
            try:
                for mode in modes:
                    script_key = 'ua_script' if mode == 'ua' else 'un_script'
                    script = self.servers[host][script_key]
                    client.start_line(f" -f {script}")
                    print(f"Started {mode.upper()} traffic on {host} using {script}")
            except STLError as e:
                print(f"Failed to start traffic on {host}: {e}")

    def stop_traffic(self):
        """Stop traffic on all servers"""
        for host, client in self.clients.items():
            try:
                client.stop()
                print(f"Stopped traffic on {host}")
            except STLError as e:
                print(f"Failed to stop traffic on {host}: {e}")

    def disconnect_all(self):
        """Disconnect from all servers"""
        for host, client in self.clients.items():
            try:
                client.disconnect()
                print(f"Disconnected from {host}")
            except STLError as e:
                print(f"Failed to disconnect from {host}: {e}")

def main():
    # Add TRex client to Python path
    sys.path.insert(0, '/home/cisco/trex/v3.06/trex_client/interactive/')

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Start TRex traffic streams')
    parser.add_argument('mode', choices=['ua', 'un', 'all'],
                       help='Traffic mode: ua (uA only), un (uN only), or all (both)')
    args = parser.parse_args()

    # Determine which modes to run
    if args.mode == 'all':
        modes = ['ua', 'un']
    else:
        modes = [args.mode]

    controller = TRexController()
    
    try:
        # Connect to all servers
        controller.connect_all()
        
        # Start traffic with specified modes
        controller.start_traffic(modes)
        
        print("\nTraffic is running. Press Enter to stop...")
        input()
        
    except KeyboardInterrupt:
        print("\nReceived interrupt signal")
    
    finally:
        # Clean up
        controller.stop_traffic()
        controller.disconnect_all()

if __name__ == "__main__":
    main() 