import os
import sys
import json
import re
import boto3
import urllib3
import urllib.parse


# Initialise the urllib3 pool manager, as we need it for all execution paths on
# this function.
# TODO: investigate how this persists in Lambda as it's outside the calling
# function
HTTP = urllib3.PoolManager()

def load_configuration():
    """Read the configuration from the environment variables and return as a
    dictionary. Maybe a bit redundant, but we might move this to a config file
    or some other config storage later on."""
    config = {}
    config['DDB_TABLE']    = os.environ['DDB_TABLE']
    config['HUMIO_SERVER'] = os.environ['HUMIO_SERVER']
    config['HUMIO_TOKEN']  = os.environ['HUMIO_TOKEN']
    config['OKTA_ORG_URL'] = os.environ['OKTA_ORG_URL']
    config['OKTA_API_KEY'] = os.environ['OKTA_API_KEY']
    return config



def setup_database_connection(config):
    """Setup connections to the DynamoDB and return client, resource objects.
    Checks that the database configured also exists and that we have write
    access.
    TODO: This should do something useful if the database doesn't already
    exist."""
    resource = boto3.resource('dynamodb')
    client   = boto3.client('dynamodb')

    try:
        client.put_item(TableName=config['DDB_TABLE'],
                        Item={'okta_org_url':   {'S': "TEST"},
                              'last_query_url': {'S': "TEST"}})
        client.get_item(TableName=config['DDB_TABLE'],
                        Key={'okta_org_url': {'S': "TEST"}})
        return {'client': client, 'resource': resource}
    except Exception as error:
        sys.stderr.write("ERROR: The function failed to connect to DynamoDB. \
                          Please check the DynamoDB table name, and \
                          permissions.\n""")
        sys.stderr.write(str(error))
        sys.exit()



def get_okta_logs(url, okta_api_key):
    """Fetches logs from Okta and returns the data (as a newline separated
    string) and the continuation url."""

    # Define the Okta request headers
    okta_headers = {'Authorization': 'SSWS ' + okta_api_key}

    # Get the messages from the Okta API
    okta_api_response = HTTP.request('GET', url, headers=okta_headers)

    # Parse the JSON content to single line messages
    okta_msgs = json.loads(okta_api_response.data.decode('utf8'))

    # Send the logs found to Humio, newline seperated
    return okta_msgs, len(okta_msgs), get_next_url(okta_api_response)



def get_next_url(response):
    """parses the "next" url from the okta response"""
    for link in response.headers['Link'].split(','):
        if "next" in link:
            url_match = re.search('<(https://.+)>;', link)
            return url_match.group(1)
    # No "next" link found (there should always be a next link!)
    return None



def get_startup_url(config, database):
    """Retrieves the checkpoint url from the persistent storage (DynamoDB)"""
    response = database['client'].get_item(TableName=config['DDB_TABLE'],
                                           Key={'okta_org_url': {'S': config['OKTA_ORG_URL']}})
    if 'Item' in response:
        # TODO: Should check that we got something sensible back from the
        # database at this point before returning.
        return response['Item']['last_query_url']['S']

    # If we didn't get back an item from DynamoDB then this is the first run
    return urllib.parse.urljoin(config['OKTA_ORG_URL'], "api/v1/logs?limit=100")



def record_continuation_url(config, database, continuation_url):
    """Writes the continuation URL to the persistent storage (DynamoDB)"""
    database['client'].put_item(TableName=config['DDB_TABLE'],
                                Item={'okta_org_url':   {'S': config['OKTA_ORG_URL']},
                                      'last_query_url': {'S': continuation_url}})



def lambda_handler(event=None, context=None):
    """This is the main function called by Lambda"""
    # Check that this function has enough time to do anything useful. The
    # timeout for this lambda function needs to be set to at least 1 minute.
    if context and context.get_remaining_time_in_millis() < 59000:
        sys.stderr.write("ERROR: This function must have a timeout >= 1 minute\n")
        sys.exit(1)

    # Read the configuration
    config = load_configuration()

    # And assume a full response before we start
    response_length = 100

    # Get the database connection
    database = setup_database_connection(config)

    # Check to see if this is a cold or warm start
    okta_url = get_startup_url(config, database)

    # Prepare the Humio headers and URL
    humio_headers = {'Authorization': 'Bearer ' + config['HUMIO_TOKEN'],
                     'Content-Type':  'application/json'}
    humio_structured_url = urllib.parse.urljoin(config['HUMIO_SERVER'], "/api/v1/ingest/humio-structured")

    # Whilst there's at least 10 seconds left before this lambda call times out
    # and we haven't reached the end of the available records from the okta api
    # we fetch logs and send them to Humio, keeping track of the last URL
    # used in DynamoDB
    while context.get_remaining_time_in_millis() > 10000 and response_length == 100:

        # Read the data from Okta
        data, response_length, okta_url = get_okta_logs(okta_url, config['OKTA_API_KEY'])

        # If there's no results to process then we can exit and be done
        if response_length == 0:
            sys.stderr.write("INFO: No new audit messages to process, exiting.\n")
            return None

        # Build the structured payload for Humio
        payload = [{'tags': {'source': 'okta-audit'}, 'events': []}]
        for event in data:
            payload[0]['events'].append({'timestamp': event['published'],
                                         'attributes': event})

        try:
            # Push the data to Humio
            HTTP.request('POST', humio_structured_url, body=json.dumps(payload).encode('utf-8'), timeout=5, headers=humio_headers)

            # Record the continuation URL in DynamoDB for a warm restart
            record_continuation_url(config, database, okta_url)

        except Exception as error:
            # TODO: This needs to capture more/all error conditions from the POST
            # Attempt to send the data to Humio failed in the timeout specified.
            # So we need to abort at this point and not record the continuation
            # url
            sys.stderr.write("ERROR: Sending data to Humio timed out, aborting.\n")
            sys.stderr.write(str(error))
            sys.exit(2)
