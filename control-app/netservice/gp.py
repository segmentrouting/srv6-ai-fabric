import json
from arango import ArangoClient

# Query DB for least utilized path parameters and return srv6 SID
def gp_calc(src, dst, user, pw, dbname):

    client = ArangoClient(hosts='http://198.18.133.104:30852')
    db = client.db(dbname, username=user, password=pw)
    cursor = db.aql.execute("""for v, e, p in 1..6 outbound """ + '"%s"' % src + """ \
            ipv4_topology options {uniqueVertices: "path", bfs: true} \
                filter v._id == """ + '"%s"' % dst + """ \
                    return distinct { path: p.edges[*].remote_node_name, sid: p.edges[*].srv6_sid, \
                        prefix_sid: p.edges[*].prefix_sid, latency: sum(p.edges[*].latency), \
                            percent_util_out: avg(p.edges[*].percent_util_out)} """)

    path = [doc for doc in cursor]
    #print("paths: ", path)
    print("number of paths found: ", len(path))
    for index in range(len(path)):
        for key in path[index]:
            #print(key, ":", path[index][key])
            ### process SR prefix sids
            if key == "prefix_sid":
                prefix_sid = path[index][key]
                for pfxsid in list(prefix_sid):
                    if pfxsid == None:
                        prefix_sid.remove(pfxsid)
                for pfxsidlast in list(prefix_sid):
                    if pfxsidlast == None:
                        prefix_sid.remove(pfxsidlast)
                    #print("prefix_sids: ", prefix_sid)

            ### process SRv6 sids
            if key == "prefix_sid":
                print("SR prefix sids for path: ", prefix_sid)
            if key == "sid":
                #print("sid: ", path[index][key])
                locators = path[index][key]
                usid_block = 'fc00:0:'
                for sid in list(locators):
                    if sid == None:
                        locators.remove(sid)
                print("SRv6 locators for path: ", locators)
                usid = []
                for s in locators:
                    if s != None and usid_block in s:
                        usid_list = s.split(usid_block)
                        #print(usid_list)
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
                #print("srv6 sid: ", srv6_sid)
                siddict = {}
                siddict['srv6_sid'] = srv6_sid
                path[index][key].append(siddict)

    pathdict = path
    #print("path: ", pathdict)
        
    pathdict = {
            'statusCode': 200,
            'source': src,
            'destination': dst,
            'path': path
        }
    print("All paths data from", src, "to", dst, "logged to log/get_paths.json" )
    pathobj = json.dumps(pathdict, indent=4)
    #print(pathobj)
    return(pathobj)
