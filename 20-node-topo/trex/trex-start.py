
import sys

# adding trex location to the system path
sys.path.insert(0, '/home/cisco/trex/v3.06/trex_client/interactive/')

from trex_stl_lib.api import *

# host00
a = STLClient(server = "172.20.7.200")
a.connect()
a.reset()
a.start_line (" -f host00/host00_uA.py --port 0")

# #host01
# b = STLClient(server = "172.20.7.201")
# b.connect()
# b.reset()
# b.start_line (" -f host01/host01_traffic.py --port 0")




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




