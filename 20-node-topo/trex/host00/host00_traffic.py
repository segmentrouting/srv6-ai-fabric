#import sys
#sys.path.append('/opt/trex/v3.04/')
from trex_stl_lib.api import *
#from trex.stl.api import *

def create_ipv6_stream(src_ip, dst_ip, pps=1000):
    # Create a base packet with IPv6
    base_pkt = Ether()/IPv6(src=src_ip, dst=dst_ip)/UDP(dport=12345, sport=54321)
    
    # Create a packet size that will result in ~10Mbps at 1000pps
    # 10Mbps = 10,000,000 bits/sec
    # At 1000pps, each packet should carry 10,000 bits = 1250 bytes
    # Subtract the size of headers to get payload size
    pad_size = 1250 - len(base_pkt)
    
    # Create a stream with the packet
    pkt = STLPktBuilder(pkt=base_pkt/('x' * pad_size))
    return STLStream(packet=pkt, mode=STLTXCont(pps=pps))

def main():
    # Create client
    client = STLClient()
    
    try:
        # Connect to server
        client.connect()
        
        # Port configurations
        port_config = {
            0: {'src': 'fc00:0:f800::2', 'dst': 'fc00:0:f800:8000::2'},
            1: {'src': 'fc00:0:f804::2', 'dst': 'fc00:0:f800:8004::2'},
            2: {'src': 'fc00:0:f808::2', 'dst': 'fc00:0:f800:8008::2'},
            3: {'src': 'fc00:0:f80c::2', 'dst': 'fc00:0:f800:800c::2'}
        }
        
        # Reset ports
        client.reset()
        
        # Add streams to ports
        for port_id, addresses in port_config.items():
            # Create stream
            stream = create_ipv6_stream(addresses['src'], addresses['dst'])
            
            # Add stream to port
            client.add_streams(stream, ports=[port_id])
            
            print(f"Added stream to port {port_id}")
            print(f"Source IP: {addresses['src']}")
            print(f"Destination IP: {addresses['dst']}\n")
        
        # Start traffic on all ports
        client.start(ports=list(port_config.keys()))
        
        print("Traffic started on all ports")
        print("Press Enter to stop...")
        input()
        
        # Stop traffic
        client.stop()
        
    except STLError as e:
        print(e)
    
    finally:
        client.disconnect()

if __name__ == "__main__":
    main() 