# Script adds srv6 sids to nodes in the graphDB

import json
from arango import ArangoClient

user = "root"
pw = "jalapeno"
dbname = "jalapeno"

client = ArangoClient(hosts='http://198.18.133.104:30852')
db = client.db(dbname, username=user, password=pw)
gpus = db.collection('gpus')

if db.has_collection('ebgp_peer_v6'):
    pr = db.collection('ebgp_peer_v6')

# if db.has_collection('ipv6_graph'):
#     ipv6graph = db.collection('ipv6_graph')

if db.has_collection('bgpv6_graph'):
    bgpv6graph = db.collection('bgpv6_graph')

pr.properties()
bgpv6graph.properties()
gpus.properties()

# Read and load the JSON file
with open('sids.json', 'r') as f:
    sid_data = json.load(f)

# Query existing peers and update with SID data
for sid in sid_data:
    try:
        # Find the matching peer document
        peer = pr.find({'_key': sid['_key']}).next()
        
        # Update the peer document with SID information
        pr.update_match(
            {'_key': sid['_key']},
            {'sids': sid['sids']}
        )
        print(f"Updated peer {sid['_key']} with SID {sid['sids']}")
    except StopIteration:
        print(f"No peer found for router_id: {sid['_key']}")
    except Exception as e:
        print(f"Error updating peer with router_id {sid['_key']}: {e}")

print("SID updates completed")

