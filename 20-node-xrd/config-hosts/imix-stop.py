import sys

# adding trex location to the system path
sys.path.insert(0, '/home/cisco/trex-v3.04/trex_client/interactive/')

from trex_stl_lib.api import *

a = STLClient(server = "172.20.99.11")
a.connect()
a.reset()
a.stop(ports = [0])

b = STLClient(server = "172.20.99.12")
b.connect()
b.reset()
b.stop(ports = [0])





