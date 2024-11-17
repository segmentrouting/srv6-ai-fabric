import subprocess

def add_linux_route(dstpfx, srv6_sid, intf, encap):
    # print("dstpfx: ", dstpfx, "srv6_sid: ", srv6_sid, "intf: ", intf, "encap: ", encap, "\n")
    if encap == "srv6":
        print("adding linux SRv6 route: ip route add", dstpfx, "encap seg6 mode encap segs", srv6_sid, "dev", intf, "\n")
        #d = subprocess.call(['sudo', 'ip -6', 'route', 'del', dstpfx])
        #a = subprocess.call(['sudo', 'ip', 'route', 'add', dstpfx, 'encap', 'seg6', 'mode', 'encap', 'segs', srv6_sid, 'dev', intf])
        #print("Show Linux Route Table: ")
        #subprocess.call(['ip', 'route'])

def add_vpp_route(dst, srv6_sid, prefix_sid, encap):

    if encap == "srv6":
        print("adding vpp sr-policy to: ", dst, ", with SRv6 encap: ", srv6_sid)
        subprocess.call(['sudo', 'vppctl', 'ip route del', dst])
        subprocess.call(['sudo', 'vppctl', 'sr steer del l3', dst])
        subprocess.call(['sudo', 'vppctl', 'sr policy del bsid 101::101', dst])
        subprocess.call(['sudo', 'vppctl', 'sr', 'policy', 'add', 'bsid', '101::101', 'next', srv6_sid, 'encap'])
        subprocess.call(['sudo', 'vppctl', 'sr', 'steer', 'l3', dst, 'via', 'bsid', '101::101'])
        print("Display VPP FIB entry: ")
        subprocess.call(['sudo', 'vppctl', 'show', 'ip', 'fib', dst])

    if encap == "sr":
        print("adding vpp route to: ", dst, "with SR label stack", prefix_sid)
        label_stack = ' '.join([str(elem) for elem in prefix_sid])
        print("label stack: ", label_stack)
        subprocess.call(['sudo', 'vppctl', 'ip route del', dst])
        subprocess.call(['sudo', 'vppctl', 'sr steer del l3', dst])
        subprocess.call(['sudo', 'vppctl', 'sr policy del bsid 101::101', dst])
        subprocess.call(['sudo', 'vppctl', 'ip route add', dst, 'via 10.101.1.2 GigabitEthernetb/0/0 out-labels', label_stack])
        print("Display VPP FIB entry: ")
        subprocess.call(['sudo', 'vppctl', 'show', 'ip', 'fib', dst])