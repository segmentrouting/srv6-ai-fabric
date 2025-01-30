import sys

# adding trex location to the system path
sys.path.insert(0, '/home/cisco/trex/v3.06/trex_client/interactive/')

from trex_stl_lib.api import *

a = STLClient(server = "172.20.7.200")
a.connect()
a.reset()
a.stop(ports = [0])




