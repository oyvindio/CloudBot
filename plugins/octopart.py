# Based on plugin by FurCode - <https://github.com/FurCode/RoboCop2>

import requests

from cloudbot import hook

API_URL = "http://octopart.com/api/v3/parts/search"


@hook.command("octopart", "octosearch")
def octopart(text, reply):
    """octopart <keyword> -- Search for any part on the Octopart database."""
    params = {
        'apikey': 'aefcd00e',
        'q': text,
        'start': 0,
        'limit': 1
    }

    try:
        request = requests.get(API_URL, params=params)
        request.raise_for_status()
    except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError) as e:
        return "Could not fetch part data: {}".format(e)

    response = request.json()

    if not response['results']:
        return "No results."

    # get part
    results = response['results']

    for result in results:
        part = result['item']

        # print matched part
        reply("{} - {} - {}".format(part['brand']['name'], part['mpn'], part['octopart_url']))
