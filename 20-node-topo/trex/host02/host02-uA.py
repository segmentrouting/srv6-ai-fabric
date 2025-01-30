from trex_stl_lib.api import *

class STLIPv6(object):

    def get_streams(self, direction=0, **kwargs):
        # Port configurations with both inner and outer IPv6 addresses
        port_config = {
            0: {
                'src': 'fc00:0:f800:8000::2',
                'dst': 'fc00:0:f800::2',
                'srv6_dst': 'fc00:0:fe00:fe00:fe00:fe00::'
            },
            1: {
                'src': 'fc00:0:f800:8004::2',
                'dst': 'fc00:0:f800:4::2',
                'srv6_dst': 'fc00:0:fe01:fe01:fe01:fe01::'
            },
            2: {
                'src': 'fc00:0:f800:8008::2',
                'dst': 'fc00:0:f800:8::2',
                'srv6_dst': 'fc00:0:fe02:fe02:fe02:fe02::'
            },
            3: {
                'src': 'fc00:0:f800:800c::2',
                'dst': 'fc00:0:f800:c::2',
                'srv6_dst': 'fc00:0:fe03:fe03:fe03:fe03::'
            }
        }

        # Create streams list
        streams = []
        
        for port_id, addresses in port_config.items():
            # Create packet with outer IPv6 (SRv6) and inner IPv6
            base_pkt = Ether()/\
                      IPv6(src=addresses['src'], dst=addresses['srv6_dst'])/\
                      IPv6(src=addresses['src'], dst=addresses['dst'])/\
                      ICMPv6EchoRequest()
            
            # Create a packet size that will result in ~10Mbps at 1000pps
            pad_size = 1250 - len(base_pkt)
            
            # Create a stream with the packet
            pkt = STLPktBuilder(pkt=base_pkt/('x' * pad_size))
            streams.append(STLStream(packet=pkt, mode=STLTXCont(pps=3)))

        return streams

def register():
    return STLIPv6()

def main():
    # Create client
    client = STLClient()
    
    try:
        # Connect to server
        client.connect()
        
        # Reset ports
        client.reset()
        
        # Create traffic profile
        profile = STLIPv6()
        streams = profile.get_streams()
        
        # Add streams to ports
        for port_id, stream in enumerate(streams):
            client.add_streams(stream, ports=[port_id])
            print(f"Added stream to port {port_id}")
        
        # Start traffic on all ports
        client.start(ports=[0,1,2,3])
        
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