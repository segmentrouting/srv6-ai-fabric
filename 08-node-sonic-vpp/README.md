# sonic-srv6


1. Deploy playbook
   - spin up clab sonic topology with ubuntu "host" containers
   - add linux bridges
   - add sonic node ssh keys to known hosts
  
2. SONiC playbook 
   - apply interface configs via config_db.json
   - run hostname and loopback shell scripts
   - add sr0 loopback interface
   - apply FRR BGP config
  
3. Ubuntu playbook (ubuntu containers attached to SONiC leaf nodes for the purpose of simulating host-based SRv6 encap/decap) - under construction




### Telemetry- under construction

```
sudo apt update
sudo apt install git dpkg-dev fakeroot debhelper build-essential:native
git clone https://github.com/sonic-net/sonic-gnmi.git

gnmi_set -username admin -password admin -update /openconfig-interfaces:interfaces/interface[name=Ethernet0]/config/mtu:@mtu.json -target_addr 127.0.0.1:8080  -insecure true -pretty


gnmi_get -username admin -password admin /openconfig-interfaces:interfaces/interface[name=Ethernet0]/config/ -target_addr 127.0.0.1:8080  -insecure true -pretty
```

4. TLS playbook
```
sudo apt install python3.10-venv
python3 -m venv venv
source venv/bin/activate
pip install ansible
cd srv6-ai-fabric/08-node-sonic-vpp/ansible/
ansible-playbook -i hosts tls-playbook.yaml -e "ansible_user=admin ansible_ssh_pass=admin ansible_sudo_pass=admin" -vv
```

```
sonic-cfggen -j telemetry-dialout.json --write-to-db 
sudo config save -y
show runningconfiguration all | grep TELEMETRY -A 11
```

5. Install pygnmi
```
pip install pygnmi
```

6. Example command:
```
pygnmicli -t clab-sonic-s00:8080 \
    -r "./certs/RootCA.crt" \
    -c "./certs/s00.crt" \
    -k "./certs/s00.key" \
    -u admin -p admin -o capabilities

```

```
pygnmicli -t clab-sonic-s00:8080 -i  -u admin -p admin -o capabilities
pygnmicli -t clab-sonic-s00:8080 -i  -u admin -p admin -o get -x /openconfig-interfaces:interfaces
pygnmicli -t clab-sonic-s00:8080 -i  -u admin -p admin -o get -x /openconfig-interfaces:interfaces/interface[name=Ethernet0]

pygnmicli -t clab-sonic-s00:8080 -i  -u admin -p admin -o get -x /openconfig-lldp:lldp/interfaces/interface[name=Ethernet8]

pygnmicli -t clab-sonic-s00:8080 -i  -u admin -p admin --gnmi-path-target CONFIG_DB -o get -x PORT/Ethernet0

pygnmicli -t clab-sonic-s00:8080 -i  -u admin -p admin --gnmi-path-target COUNTERS_DB -o get -x COUNTERS/Ethernet0
```

```
gnmic -a clab-sonic-s00:8080 \
    --tls-ca "./certs/RootCA.crt" \
    --tls-cert "./certs/s00.crt" \
    --tls-key "./certs/s00.key" \
    -u admin -p admin \
    capabilities

```