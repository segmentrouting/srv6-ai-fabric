import json
from arango import ArangoClient
from . import add_route

# Query DB for least utilized path parameters and return srv6 and sr sid info
def lu_calc(frm, to, dst, user, pw, dbname, intf, dataplane, encap):

    client = ArangoClient(hosts='http://198.18.133.104:30852')
    db = client.db(dbname, username=user, password=pw)
    cursor = db.aql.execute("""for v, e in any shortest_path """ + '"%s"' % frm + """ \
        to """ + '"%s"' % to + """ bgpv6_graph \
            options { weightAttribute: 'load' } \
                return { node: v._key, edge: e._key, name: v.name, sid: v.sids[0].srv6_sid, load: e.load } """)
    path = [doc for doc in cursor]
    # print(json.dumps(path, indent=4))

    # Update edge documents with load value
    for doc in path:
        if doc.get('edge'):  # Only process if edge key exists
            # Get current edge document
            edge_doc = db.collection('bgpv6_graph').get({'_key': doc['edge']})
            # Get current load value, default to 0 if it doesn't exist
            current_load = edge_doc.get('load', 0)
            # Update with incremented load
            db.collection('bgpv6_graph').update_match(
                {'_key': doc['edge']},
                {'load': current_load + 10}
            )   
            #print("load updated for edge: ", doc['edge'])

    # Calculate average load after updates
    total_load = 0
    edge_count = 0
    for doc in path:
        if doc.get('edge'):  # Only count edges
            edge_doc = db.collection('bgpv6_graph').get({'_key': doc['edge']})
            total_load += edge_doc.get('load', 0)  # This will now get the updated load values
            edge_count += 1
    
    avg_load = total_load / edge_count if edge_count > 0 else 0
    print(f"Average load across path: {avg_load}\n")

    sid = 'sid'
    usid_block = 'fc00:0:'
    locators = [a_dict[sid] for a_dict in path]
    for sid in list(locators):
        if sid == None:
            locators.remove(sid)
    print("locators: ", locators)

    usid = []
    for s in locators:
        if s != None and usid_block in s:
            usid_list = s.split(usid_block)
            sid = usid_list[1]
            usid_int = sid.split(':')
            u = int(usid_int[0])
            usid.append(u)

    ipv6_separator = ":"

    sidlist = ""
    for word in usid:
        sidlist += str(word) + ":"
    #print(sidlist)

    srv6_sid = usid_block + sidlist + ipv6_separator
    print("srv6 sid: ", srv6_sid)

    pathdict = {
            'statusCode': 200,
            'source': frm,
            'destination': dst,
            'sid': srv6_sid,
            'path': path
        }

    #print("route_add parameters = sid: ", srv6_sid, "sr_label_stack: ", prefix_sid, "dest: ", dst, "intf: ", intf, "dataplane: ", dataplane)
    if dataplane == "linux":
        route_add = add_route.add_linux_route(dst, srv6_sid, intf, encap)
    if dataplane == "vpp":
        route_add = add_route.add_vpp_route(dst, srv6_sid, encap)
    pathobj = json.dumps(pathdict, indent=4)
    return(pathobj)


    
