#!/usr/bin/env python3
"""
Test script to authenticate with Plex using PIN-based login.
This mimics the app's /api/plex/pin/start and /api/plex/pin/status flows.
"""

import sys
import time
import traceback
from plexapi.myplex import MyPlexAccount, MyPlexPinLogin


def main():
    print('Starting Plex PIN login flow...')
    try:
        pinlogin = MyPlexPinLogin(oauth=False)
    except Exception as exc:
        print(f'Failed to create MyPlexPinLogin: {exc}', file=sys.stderr)
        traceback.print_exc()
        return 1

    pin = getattr(pinlogin, 'pin', None)
    if not pin:
        print('Failed to obtain PIN from Plex', file=sys.stderr)
        return 1

    print(f'\nPlease go to the following URL in your browser and enter the PIN: {pin}')
    print(f'  https://plex.tv/link')
    print('Then return here and wait for authentication to complete.')

    while True:
        if getattr(pinlogin, 'expired', False):
            print('\nPIN has expired. Please rerun this script to start again.', file=sys.stderr)
            return 2

        try:
            if pinlogin.checkLogin():
                token = getattr(pinlogin, 'token', None)
                if not token:
                    print('Login succeeded but no token was returned.', file=sys.stderr)
                    return 3

                print('\nLogin complete!')
                print(f'Plex token: {token}')

                try:
                    account = MyPlexAccount(token=token)
                except Exception as exc:
                    print(f'Failed to create MyPlexAccount: {exc}', file=sys.stderr)
                    traceback.print_exc()
                    return 4

                print(f'Authenticated Plex username: {getattr(account, "username", None)}')
                print(f'Authenticated Plex title: {getattr(account, "title", None)}')

                print('\nFetching available Plex resources...')
                try:
                    resources = account.resources()
                except Exception as exc:
                    print(f'Failed to fetch resources: {exc}', file=sys.stderr)
                    traceback.print_exc()
                    return 5

                server_resources = [r for r in resources if 'server' in (getattr(r, 'provides', '') or '')]
                if server_resources:
                    print(f'Found {len(server_resources)} Plex server resource(s):')
                    for idx, resource in enumerate(server_resources, 1):
                        print(f'[{idx}] {getattr(resource, "name", None)} ({getattr(resource, "product", None)})')
                        connections = getattr(resource, 'connections', []) or []
                        for conn in connections:
                            uri = getattr(conn, 'uri', None)
                            local = getattr(conn, 'local', False)
                            relay = getattr(conn, 'relay', False)
                            print(f'     - {uri} {'(LOCAL)' if local else ''}{' (RELAY)' if relay else ''}')

                    local_conn = None
                    for resource in server_resources:
                        for conn in getattr(resource, 'connections', []) or []:
                            if getattr(conn, 'local', False):
                                local_conn = conn
                                break
                        if local_conn:
                            break

                    if local_conn:
                        print(f'\nSelected local server URI: {getattr(local_conn, "uri", None)}')
                    else:
                        print('\nNo local server connection found. Use one of the above URIs manually.')
                else:
                    print('No Plex server resources were found for this account.')

                return 0

            print('PIN not yet authorized. Polling again in 5 seconds...')
        except Exception as exc:
            print(f'Error checking PIN login status: {exc}', file=sys.stderr)
            traceback.print_exc()
            return 6

        time.sleep(5)


if __name__ == '__main__':
    sys.exit(main())
