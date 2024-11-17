from arango import ArangoClient

def reset_load(user, pw, dbname):
    # Initialize the client for ArangoDB
    client = ArangoClient(hosts='http://198.18.133.104:30852')
    
    # Connect to the database
    db = client.db(dbname, username=user, password=pw)
    
    # Update all documents in the ipv6_graph collection, setting load to 0
    cursor = db.aql.execute("""
        FOR doc IN ipv6_graph
        UPDATE doc WITH { load: 0 } IN ipv6_graph
        RETURN NEW
    """)
    
    print("All load values have been reset to 0")

if __name__ == "__main__":
    # You can modify these values or pass them as arguments
    USER = "root"
    PASSWORD = "jalapeno"
    DBNAME = "jalapeno"
    
    reset_load(USER, PASSWORD, DBNAME) 