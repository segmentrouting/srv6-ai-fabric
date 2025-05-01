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
  
3. TRex playbook

4. TLS playbook
```
sudo apt install python3.10-venv
python3 -m venv venv
source venv/bin/activate
pip install ansible
cd srv6-ai-fabric/08-node-sonic-vpp/ansible/
ansible-playbook -i hosts tls-playbook.yaml -e "ansible_user=admin ansible_ssh_pass=admin ansible_sudo_pass=admin" -vv

```