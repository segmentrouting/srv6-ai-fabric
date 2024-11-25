# Script writes site and link meta data into the Arango graphDB
# requires https://pypi.org/project/python-arango/
# python3 add_meta_data.py

import json
from arango import ArangoClient

user = "root"
pw = "jalapeno"
dbname = "jalapeno"

client = ArangoClient(hosts='http://198.18.133.104:30852')
db = client.db(dbname, username=user, password=pw)
gpus = db.collection('gpus')

# if db.has_collection('ls_node_extended'):
#     lsn = db.collection('ls_node_extended')

if db.has_collection('peer'):
    pr = db.collection('peer')

# if db.has_collection('ipv6_graph'):
#     ipv6graph = db.collection('ipv6_graph')

if db.has_collection('bgpv6_graph'):
    bgpv6graph = db.collection('bgpv6_graph')

if not db.has_collection('gpus'):
    gpus = db.create_collection('gpus')
else:
    gpus = db.collection('gpus')

# lsn.properties()
# ipv6graph.properties()
bgpv6graph.properties()
gpus.properties()

# Read and load the JSON file
with open('gpus.json', 'r') as f:
    gpu_data = json.load(f)

# Insert each GPU document
for gpu in gpu_data:
    try:
        gpus.insert(gpu)
    except Exception as e:
        print(f"Error inserting {gpu['_key']}: {e}")

print("GPU documents added")

with open('gpu-edge.json', 'r') as f:
    gpu_edge_data = json.load(f)

# change this for loop for ipv6 or bgpv6
for edge in gpu_edge_data:
    try:
        # ipv6graph.insert(edge)
        bgpv6graph.insert(edge)
    except Exception as e:
        print(f"Error inserting {edge['_key']}: {e}")

# print("GPU edges added to ipv6_graph")
print("GPU edges added to bgpv6_graph")
