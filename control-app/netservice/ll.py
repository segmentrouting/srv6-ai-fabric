import json
from arango import ArangoClient
from math import ceil
from . import add_route

# Query DB for low latency path parameters and return srv6 and sr sid info
def ll_calc(src_id, dst_id, dst, user, pw, dbname, intf, dataplane, encap):

    client = ArangoClient(hosts='http://198.18.133.104:30852')
    db = client.db(dbname, username=user, password=pw)
    cursor = db.aql.execute("""for v, e in outbound shortest_path """ + '"%s"' % src_id + """ \
        to """ + '"%s"' % dst_id + """ ipv4_topology \
            options { weightAttribute: 'latency' } \
                return { node: v._key, name: v.name, sid: e.srv6_sid, prefix_sid: e.prefix_sid, latency: e.latency } """)
    path = [doc for doc in cursor]
    #print("path: ", path)
    hopcount = len(path)
    #print("hops: ", hopcount)
    pq = ceil((hopcount/2)-1)
    #print(pq)
    pq_node = (path[pq])
    #print("pqnode: ", pq_node)
    sid = 'sid'
    usid_block = 'fc00:0:'
    locators = [a_dict[sid] for a_dict in path]
    for sid in list(locators):
        if sid == None:
            locators.remove(sid)
    print("locators: ", locators)

    prefix_sid = 'prefix_sid'
    prefix_sid = [a_dict[prefix_sid] for a_dict in path]
    for ps in list(prefix_sid):
        if ps == None:
            prefix_sid.remove(ps)
    print("prefix_sids: ", prefix_sid)

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
            'source': src_id,
            'destination': dst_id,
            'sid': srv6_sid,
            'sr_label_stack': prefix_sid,
            'path': path
        }

    #print("route_add parameters = sid: ", srv6_sid, "sr_label_stack: ", prefix_sid, "dest: ", dst, "intf: ", intf, "dataplane: ", dataplane)
    if dataplane == "linux":
        route_add = add_route.add_linux_route(dst, srv6_sid, prefix_sid, intf, encap)
    if dataplane == "vpp":
        route_add = add_route.add_vpp_route(dst, srv6_sid, prefix_sid, encap)
    pathobj = json.dumps(pathdict, indent=4)
    return(pathobj)
