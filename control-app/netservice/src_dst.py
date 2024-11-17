from arango import ArangoClient

def get_src_dst(src, prefix, user, pw, dbname):
    client = ArangoClient(hosts='http://198.18.133.104:30852')
    db = client.db(dbname, username=user, password=pw)
    print(src, prefix)
    aql = db.aql
    cursor = db.aql.execute("""for u in unicast_prefix_v6 filter u.prefix == """  + '"%s"' % src +  """ \
        return { id: u._id, src_peer: u.peer_ip } """)
    src_dict = [doc for doc in cursor]
    # print("""for u in unicast_prefix_v4 filter u.prefix == """  + '"%s"' % src +  """ \
    #     return { id: u._id, src_peer: u.peer_ip } """)
    print("src data: ", src_dict)

    # Get destination prefix ID to end graph traversal
    aql = db.aql
    cursor = db.aql.execute("""for u in unicast_prefix_v6 filter u.prefix == """  + '"%s"' % prefix +  """ \
        return { id: u._id, dst_peer: u.peer_ip } """)
    dst_dict = [doc for doc in cursor]
    print("dest data: ", dst_dict)

    id = "id"
    src_peer = "src_peer"
    dst_peer = "dst_peer"

    s = src_dict[0]
    d = dst_dict[0]

    if s[src_peer] == d[dst_peer]:
        print(""" 
        Source and destination are reachable via the same router, no optimization available
        
        """)
    else:
        src = s[id]
        dst = d[id]
        return src, dst