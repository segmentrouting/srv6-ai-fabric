### sonic-vpp base topology

![Sonic VPP Test Topology](topology.png)


1. Install VXR
2. Create VXR topology yaml file: [example](./sonic-vxr.yml)

```
sudo vxr.py start <filename>
```

3. It can take up to 7 or 8 minutes for all nodes to come up and SWSS and FRR be ready
   
4. Run Ansible sonic-vpp playbook to configre nodes (config_db.json and frr.conf). Note, the playbook also creates user cisco with password cisco123 and sudo permission for easier login to the devices.

```
cd ansible
ansible-playbook sonic-vpp-playbook.yml
```

5. Ansible playbook to deploy frr SRv6 configuration - hopefully this step will be removed in the future
```
cd ansible
ansible-playbook sonic-srv6-playbook.yml
```

### Troubleshooting

Accessing VPP 
```
docker exec -it syncd bash
vppctl -s /run/vpp/cli.sock
```

VPP commands
```
show sr localsids
show sr policies
show sr steering-policies
show sr encaps source addr 
```

SAI Redis logs
```
sudo tail -f /var/log/syslog /var/log/swss/sairedis.rec 
```