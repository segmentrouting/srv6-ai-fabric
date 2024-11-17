import argparse
import json
import sys
import time
from netservice import src_dst, lu, ll, ds, gp

### Jalapeno/SDN client ###

def main():
    # Handle cli options passed in
    parser = argparse.ArgumentParser(
        prog = 'Jalapeno client',
        description = 'takes command line input and calls path calculator functions',
        epilog = 'jalapeno.py -f <json file> -e <sr or srv6> -s <ll, lu, ds, or gp> ')
    parser.add_argument("-e", help="encapsulation type <sr> <srv6>")
    parser.add_argument("-f", help="json file with src, dst, parameters")  
    parser.add_argument("-s", help="requested network service: ll = low_latency, lu = least_utilized, ds = data_sovereignty, gp = get_paths)")
    args = parser.parse_args()

    encap = args.e
    file = args.f
    service = args.s

    # Check that the required input arguments were passed in
    if not encap or not file or not service:
        print("Required input elements encapsulation type, input file, and/or service type were not entered")
        print("jalapeno.py -f <json file> -e <sr | srv6> -s <ll, lu, ds, or gp>")
        exit()


    username = "username"
    password = "password"
    database = "database"
    _from = "_from"
    _to = "_to"
    source = "source"
    dstpfx = "destination"
    interface = "interface"
    dataplane = "dataplane"

    f = open(file)
    sd = json.load(f)

    user = sd[username]
    pw = sd[password]
    dbname = sd[database]
    
    # Check if input is a list of requests
    requests = sd.get('requests', [sd])  # If no 'requests' key, treat single request as list
    
    for request in requests:
        # Get parameters for this request
        frm = request[_from]
        to = request[_to]
        dst = request[dstpfx]
        intf = request[interface]
        dp = request[dataplane]

        if service == "lu":
            print("\n", "Elephant Flow Load Balancing Service")
            print("from: ", frm, "to: ", to)
            srv6_lu = lu.lu_calc(frm, to, dst, user, pw, dbname, intf, dp, encap)
            
            # Write results to log file
            with open('log/least_util.json', 'a') as f:
                f.write(str(srv6_lu) + '\n')
                f.flush()
            
            # Small delay to ensure DB updates are complete
            time.sleep(0.1)

if __name__ == '__main__':
    main()