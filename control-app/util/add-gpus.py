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

if db.has_collection('ls_node_extended'):
    lsn = db.collection('ls_node_extended')

if db.has_collection('peer'):
    pr = db.collection('peer')

if db.has_collection('ipv6_graph'):
    ipv6graph = db.collection('ipv6_graph')

if not db.has_collection('gpus'):
    gpus = db.create_collection('gpus')
else:
    gpus = db.collection('gpus')

lsn.properties()
ipv6graph.properties()
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

for edge in gpu_edge_data:
    try:
        ipv6graph.insert(edge)
    except Exception as e:
        print(f"Error inserting {edge['_key']}: {e}")

print("GPU edges added to ipv6_graph")

# print("adding addresses, country codes, and synthetic latency data to the graph")

# # get the ls_node DB key and populate the document with location and latency data
# r01 = lsn.get('2_0_0_0000.0000.0001')
# r01['location_id'] = 'AMS001'
# r01['country_code'] = 'NLD'
# r01['address'] = "Frederiksplein 42, 1017 XN Amsterdam, Netherlands"
# lsn.update(r01)

# r02 = lsn.get('2_0_0_0000.0000.0002')
# r02['location_id'] = 'BML001'
# r02['country_code'] = 'DEU'
# r02['address'] = "Albrechtstraße 110, 12103 Berlin, Germany"
# lsn.update(r02)

# r03 = lsn.get('2_0_0_0000.0000.0003')
# r03['location_id'] = 'IEV001'
# r03['country_code'] = 'UKR'
# r03['address'] = "O.Gonchara str, Kyiv,Ukraine"
# lsn.update(r03)

# # Outbound path (left to right on diagram)

# print("adding location, country codes, latency, and link utilization data")

# ipv4topo0102 = ipv4topo.get("2_0_0_0_0000.0000.0001_10.1.1.0_0000.0000.0002_10.1.1.1")
# ipv4topo0102['latency'] = 10
# ipv4topo0102['percent_util_out'] = 35
# ipv4topo0102['country_codes'] = ['NLD', 'DEU']
# ipv4topo.update(ipv4topo0102)

# ipv4topo0105 = ipv4topo.get("2_0_0_0_0000.0000.0001_10.1.1.8_0000.0000.0005_10.1.1.9")
# ipv4topo0105['latency'] = 5
# ipv4topo0105['percent_util_out'] = 55
# ipv4topo0105['country_codes'] = ['NLD', 'GBR']
# ipv4topo.update(ipv4topo0105)

# # Return path

# ipv4topo0201 = ipv4topo.get("2_0_0_0_0000.0000.0002_10.1.1.1_0000.0000.0001_10.1.1.0")
# ipv4topo0201['latency'] = 10
# ipv4topo0201['percent_util_out'] = 30
# ipv4topo0201['country_codes'] = ['NLD', 'DEU']
# ipv4topo.update(ipv4topo0201)

# print("meta data added")