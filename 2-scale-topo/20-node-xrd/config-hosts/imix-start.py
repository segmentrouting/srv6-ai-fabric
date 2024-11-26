
import sys

# adding trex location to the system path
sys.path.insert(0, '/home/cisco/trex-v3.04/trex_client/interactive/')

from trex_stl_lib.api import *

# trex01
a = STLClient(server = "172.20.99.11")
a.connect()
a.reset()
a.start_line (" -f trex01/1-imix.py -m 3mbps --port 0")

# trex02
b = STLClient(server = "172.20.99.12")
b.connect()
b.reset()
b.start_line (" -f trex02/2-imix.py -m 4mbps --port 0")


# a.stop(ports = [0])
# b.stop(ports = [0])
# c.stop(ports = [0])
# d.stop(ports = [0])
# e.stop(ports = [0])
# f.stop(ports = [0])
# g.stop(ports = [0])
# h.stop(ports = [0])
# i.stop(ports = [0])
# j.stop(ports = [0])




