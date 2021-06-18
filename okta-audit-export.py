import os
import sys
import json
import re
import urllib3
import urllib.parse
import time
import argparse
import datetime

# Caoture the start time of the script so we can check for timeout
STARTTIME = datetime.datetime.now()

# Initialise the urllib3 pool manager, as we need it for all execution paths on
# this function.
HTTP = urllib3.PoolManager()

CONFIG = [
    "okta-org-host",
    "okta-api-key",
    "timeout",
]

# Where to place the PID file
PID_FILE = os.path.join(os.path.expanduser("~"), ".okta-to-humio.pid")

# Maximum number of events to fetch from Okta per request
# Cannot be more than 1000. Recommend 1000.
OKTA_REQUEST_LIMIT = 1000


def is_config(path):
    """Check that the path provided is a valid config file with the right content"""
    try:
        config = load_config(path)
        # Check all parameters are set
        if not all(x in config for x in CONFIG):
            raise argparse.ArgumentTypeError("Config file is incomplete.")
    except FileNotFoundError:
        # TODO: if the config file is missing we should have an option to iniailise it
        raise argparse.ArgumentTypeError("Config file is missing.")
    except Exception as error:
        # The config file exists but is broken in some other way
        raise argparse.ArgumentTypeError(f"Config file is unreadable {error}.")

    # Everything looks OK
    return path


def load_config(path):
    with open(path) as config_f:
        # Load as JSON object
        config = json.load(config_f)
        config["config-file"] = path
    return config


def write_config(path, config):
    with open(path, "w") as config_f:
        json.dump(config, config_f, indent=4, sort_keys=True)


def setup_args():
    parser = argparse.ArgumentParser(
        description="This script is used to export Okta audit logs as NDJSON to stdout."
    )

    # Source Okta environment system where the logs will be fetched from
    parser.add_argument(
        "config-file",
        type=is_config,
        action="store",
        help="The full or relative path to the configuration file for this script. If the file does \
              not exist it will be initialized with the default content.",
    )

    # Build the argument list
    return vars(parser.parse_args())


def write_pid():
    """Writes a PID to /var/run/okta-to-humio.pid
    If the PID file already exists: return False
    If the PID is created: return True
    Any other error: undefined"""

    # PID file already exists? We don't actually care if the PID matches because we only check this on startup
    if os.path.isfile(PID_FILE):
        return False

    # Get the PID
    pid = os.getpid()
    sys.stderr.write(f"The PID is: {pid}\n")

    # Create the PID file
    with open(PID_FILE, "w") as f:
        f.write(f"{pid}\n")

    # Everything went file, so ...
    return True


def delete_pid():
    os.remove(PID_FILE)


def get_okta_url(config):
    """Returns the Okta URL to use to get the next batch of events"""
    if "continuation-url" in config:
        return config["continuation-url"]
    else:
        # This must be the first run, so start with the basic URL
        return urllib.parse.urljoin(
            config["okta-org-host"], f"api/v1/logs?limit={OKTA_REQUEST_LIMIT}"
        )


def get_okta_logs(config):
    """Fetches logs from Okta and returns the data (as a newline separated
    string) and the continuation url."""

    # Define the Okta request headers
    okta_headers = {"Authorization": "SSWS " + config["okta-api-key"]}

    # Get the messages from the Okta API
    response = HTTP.request("GET", get_okta_url(config), headers=okta_headers)

    # Parse the JSON content to single line messages
    events = json.loads(response.data.decode("utf8"))

    # Send the logs found to Humio, newline seperated
    return events, get_next_url(response)


def get_next_url(response):
    """parses the "next" url from the okta response"""
    try:
        for link in response.headers["Link"].split(","):
            if "next" in link:
                url_match = re.search("<(https://.+)>;", link)
                return url_match.group(1)
    except KeyError as e:
        error_response = json.loads(response.data.decode("utf8"))
        if error_response["errorCode"] == "E0000047":
            sys.stderr.write("ERROR: Okta API Rate Limit Exceeded, exiting.\n")
        else:
            sys.stderr.write("Unknown Error occured from Okta API, details:\n")
            sys.stderr.write(response.data.decode("utf8"))
            sys.stderr.write("\n")
        sys.exit(1)
    # No "next" link found (there should always be a next link!)
    return None


if __name__ == "__main__":
    """Running as a script"""

    # Write the PID file to make sure we know we're running
    if not write_pid():
        sys.stderr.write(
            f"It looks like this script is already running! If you are certain that is not the \
case please delete the file {PID_FILE} and try again.\n"
        )
        sys.exit(99)

    # Capture "now" so we can measure timeout
    startTime = datetime.datetime.now()

    # Parse the command line arguments
    args = setup_args()

    # Load the config file
    config = load_config(args["config-file"])
    sys.stderr.write(json.dumps(config, indent=4, sort_keys=True) + "\n")

    # The main loop where we process batches of events from Okta so long as the timeout hasn't been reached
    while datetime.datetime.now() < (
        startTime + datetime.timedelta(seconds=config["timeout"])
    ):
        sys.stderr.write("Fetching events from Okta ...")
        # Get a batch of Okta events
        data, config["continuation-url"] = get_okta_logs(config)
        sys.stderr.write(" %d events returned.\n" % len(data))

        # If we didn't get any events then we're done with this run of the script
        if len(data) == 0:
            sys.stderr.write("No new audit messages to process, exiting.\n")
            break

        # Print events as NDJSON to stdout
        sys.stderr.write("Printing %d events to stdout ..." % len(data))
        for event in data:
            print(event)
        sys.stderr.write(" done.\n")

        # Update the config with the latest checkpoint data
        write_config(args["config-file"], config)

        # If the last batch had < LIMIT number of events we can assume we're at the end of the
        # audit log for now, so break rather than waste an API request
        if len(data) < OKTA_REQUEST_LIMIT:
            sys.stderr.write("Probably no new audit messages to process, exiting.\n")
            break

    # Finally write out the last version of the config and delete the PID and exit
    write_config(args["config-file"], config)
    delete_pid()
