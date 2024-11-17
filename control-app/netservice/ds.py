import json
from arango import ArangoClient
from . import add_route

### SRv6 Data Sovereignty
# Query DB for a path that avoids a given country and return srv6 and sr sid info
def ds_calc(src_id, dst_id, dst, user, pw, dbname, ctr, intf, dataplane, encap):
    print("dst: ", dst)
    client = ArangoClient(hosts='http://198.18.133.104:30852')
    db = client.db(dbname, username=user, password=pw)
    cursor = db.aql.execute("""for p in outbound k_shortest_paths \
        """ + '"%s"' % src_id + """ to """ + '"%s"' % dst_id + """ ipv4_topology \
            options {uniqueVertices: "path", bfs: true} \
            filter p.edges[*].country_codes !like "%"""+'%s' % ctr +"""%" limit 1 \
                return { path: p.edges[*].remote_node_name, sid: p.edges[*].srv6_sid, prefix_sid: p.edges[*].prefix_sid, \
                    countries_traversed: p.edges[*].country_codes[*], latency: sum(p.edges[*].latency), \
                        percent_util_out: avg(p.edges[*].percent_util_out)} """)

    path = [doc for doc in cursor]
    print("path: ", path)

    pdict = path[0]

    ### SRv6 SID processing
    locators = pdict['sid']
    usid_block = 'fc00:0:'

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

    srv6_sid = usid_block + sidlist + ipv6_separator
    print("srv6 sid: ", srv6_sid)
    #print("path: ", path)

    siddict = {}
    siddict['srv6_sid'] = srv6_sid
    path.append(siddict)

    ### SR MPLS processing
    prefix_sid = pdict['prefix_sid']
    for sid in list(prefix_sid):
        if sid == None:
            prefix_sid.remove(sid)
    print("prefix_sids: ", prefix_sid)

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


