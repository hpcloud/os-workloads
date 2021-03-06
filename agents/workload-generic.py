#!/usr/bin/env python

# Copyright 2015 Hewlett-Packard Development Company, L.P.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import json
import argparse
import urllib2
import sys
import os
import re
import socket
import keystoneclient.v2_0
import novaclient.v1_1
import time
import random
import string


def get_nova():

	keystone = keystoneclient.v2_0.client.Client(username=os.getenv("OS_USERNAME"),
							password=os.getenv("OS_PASSWORD"),
							tenant_id=os.getenv("OS_TENANT_ID"),
							auth_url=os.getenv("OS_AUTH_URL"))
	
	compute_catalog = keystone.service_catalog.get_endpoints()['computev21']
	
	cluster_endpoint = None
	
	for endpoint in compute_catalog:
		if endpoint['region'] == os.getenv("OS_REGION_NAME"):
			cluster_endpoint = endpoint

	return (keystone.auth_token, cluster_endpoint)

# Register Workload
def register(workload, priority):
	"""
	Register a new workload with the workloads endpoint.
	"""
	print "Registering "+workload

	auth_token, cluster_endpoint = get_nova()

	request = urllib2.Request(cluster_endpoint["publicURL"]+"/os-workloads",
		json.dumps({"workload":{"name":workload,"priority":priority}}), {'Content-type':'application/json','X-Auth-Token':auth_token})
	request.get_method = lambda: "POST"
	try:
		response = urllib2.urlopen(request).read()
	except urllib2.HTTPError, e:
		raise StandardError("HTTP Error from workload service: "+str(e))
	
	response_json = json.loads(response)

	config = open('workload-generic.cfg', 'w')
	workload = {"name":response_json['workload']['name'],"id":response_json['workload']['id']}
	config.write(json.dumps(workload))
	config.close()
	print "Wrote configuration"


def list_workloads():
	auth_token, cluster_endpoint = get_nova()

	request = urllib2.Request(cluster_endpoint["publicURL"]+"/os-workloads",
		None, {'X-Auth-Token':auth_token})

	try:
		response = urllib2.urlopen(request).read()
	except urllib2.HTTPError, e:
		raise StandardError("HTTP Error from workload service: "+str(e))
		
	response_json = json.loads(response)
	print json.dumps(response_json, sort_keys=True, indent=4)

def delete():
	"""
	Delete a workload.
	"""
	config = json.loads(open("workload-generic.cfg").read())
	auth_token, cluster_endpoint = get_nova()
	request = urllib2.Request(cluster_endpoint["publicURL"]+"/os-workloads/"+str(config['id']),
		None, {'X-Auth-Token':auth_token})
	request.get_method = lambda: "DELETE"
	try:
		response = urllib2.urlopen(request).read()
	except urllib2.HTTPError, e:
		raise StandardError("HTTP Error from workload service: "+str(e))
		
	response_json = json.loads(response)
	print json.dumps(response_json, sort_keys=True, indent=4)

def watch():
	"""

	"""
	# Watch Workload

	config = json.loads(open("workload-generic.cfg").read())

	auth_token, cluster_endpoint = get_nova()
		
	keystone_timeout = time.time()

	while True:

		if time.time() - keystone_timeout > 300:
			auth_token, cluster_endpoint = get_nova()
			keystone_timeout = time.time()

		#  Watch for new orders
		#try:
		request = urllib2.Request(cluster_endpoint["publicURL"]+"/os-workloads/"+str(config['id']),
			None, {'X-Auth-Token':auth_token})
		response = urllib2.urlopen(request).read()
		response_json = json.loads(response)
		orders = response_json['orders']
		#except:
		#	orders = None

		#  Execute orders (spin up instances, spin down instances)
		if orders:
			for order in orders:
				print "New order: "+str(order['id'])
				if order['instances'] > 0:
					create_instances(order['instances'],config)
				if order['instances'] < 0:
					delete_instances(order['instances']*-1,config)
				acknowledge_order(config,order,auth_token,cluster_endpoint)

		time.sleep(2)

def acknowledge_order(config,order,auth_token, cluster_endpoint):
	print "Acknowledging Order "+str(order['id'])
	request = urllib2.Request(cluster_endpoint["publicURL"]+"/os-workloads/"+str(config['id']),
		json.dumps({"order": [{"id": order['id'], "status": "FILLED"}]}), {'Content-Type':'application/json','X-Auth-Token':auth_token})
	request.get_method = lambda: "PUT"
	response = urllib2.urlopen(request).read()
	return True

def create_instances(count,config):
	requested_image = None
	nova = novaclient.v1_1.client.Client(os.getenv('OS_USERNAME'),os.getenv('OS_PASSWORD'),os.getenv('OS_TENANT_NAME'),auth_url=os.getenv('OS_AUTH_URL'))

	for image in nova.images.list():
		if "Ubuntu 14.04.1 Server" in image.name:
			requested_image = image
	
	requested_group = None

	for group in nova.security_groups.list():
		if "default" in group.name:
			requested_group = group

	requested_flavor = None
	
	for flavor in nova.flavors.list():
		if flavor.name == "m1.medium":
			requested_flavor = flavor

	for c in xrange(count):
		server_name = config['name'] + "-" + ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(5))
		try:
			my_server = nova.servers.create(server_name,
									requested_image,
									requested_flavor,
									security_groups=['default'])
			print "Creating Server: "+server_name
			time.sleep(2)
			server = nova.servers.get(my_server)
		except:
			print "Exception on create, skipping server."
			time.sleep(20)

def delete_instances(count,config):
	c = count
	nova = novaclient.v1_1.client.Client(os.getenv('OS_USERNAME'),os.getenv('OS_PASSWORD'),os.getenv('OS_TENANT_NAME'),auth_url=os.getenv('OS_AUTH_URL'))

	for server in nova.servers.list():
		if server.name.startswith(config['name']) and c > 0:
			print "Deleting "+server.name
			nova.servers.delete(server)
			c = c - 1
			time.sleep(1)


# Request Order

def order(order):
	"""
	Place an order with the service.
	"""
	auth_token, cluster_endpoint = get_nova()
	config = json.loads(open("workload-generic.cfg").read())
	print 
	request = urllib2.Request(cluster_endpoint["publicURL"]+"/os-workloads/"+str(config['id']),
		json.dumps({"order": [{"instances": int(order), "memory_mb": 4096}]}), {'Content-Type':'application/json','X-Auth-Token':auth_token})
	request.get_method = lambda: "PUT"
	response = urllib2.urlopen(request).read()
	response_json = json.loads(response)
	print json.dumps(response_json, sort_keys=True, indent=4)

if __name__ == '__main__':
	
	# We roll unbuffered.
	
	sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
	socket._fileobject.default_bufsize = 0
	
	# Parse our arguments.
	
	parser = argparse.ArgumentParser(
		formatter_class=argparse.RawDescriptionHelpFormatter,
		description='''
Register:	workload.py register 'workload name' priority
Watch:		workload.py watch
Order:		workload.py order -2
List:		workload.py list
Delete:		workload.py delete
''',
		epilog='''
note:
  OS_USERNAME and OS_PASSWORD or OS_ACCESSKEY and OS_SECRETKEY must be set,
  as well as OS_TENANT_ID, OS_IDENTITY_URL and OS_WORKLOAD_URL.
	'''	)
	parser.add_argument('arguments', metavar='arguments', type=str, nargs='+',
		help="command")
	args = parser.parse_args()

	if (args.arguments[0] == "register"):
		pri = 1
		if len(args.arguments) == 3:
			pri = int(args.arguments[2])
		register(args.arguments[1],pri)

	if (args.arguments[0] == "watch"):
		watch()

	if (args.arguments[0] == "delete"):
		delete()

	if (args.arguments[0] == "order"):
		order(args.arguments[1])

	if (args.arguments[0] == "list"):
		list_workloads()

