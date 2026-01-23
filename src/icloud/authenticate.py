"""Handles username/password authentication and two-step authentication"""
import sys
import click
import logging

import pyicloud
import constants as constants
import pyicloud.utils as utils
from pyicloud.exceptions import PyiCloud2SARequiredException
from pyicloud.exceptions import PyiCloudFailedLoginException
from pyicloud.exceptions import PyiCloudNoStoredPasswordAvailableException

def authenticate(
    username,
    password,
    cookie_directory=None,
    raise_authorization_exception=False,
    client_id=None,
    unverified_https=False
):
    logger = logging.getLogger(__name__)

    """Authenticate with iCloud username and password"""
    logger.debug("Authenticating...")

    failure_count = 0
    while True:
        try:
            api = pyicloud.PyiCloudService(
                username,
                password,
                cookie_directory=cookie_directory,
                client_id=client_id,
                verify=not unverified_https)
                
            if api.requires_2fa:
                # fmt: off
                print("\nTwo-factor (2FA) authentication required.")
                # fmt: on
                if raise_authorization_exception:
                    raise PyiCloud2SARequiredException(username)

                code = input("\nPlease enter verification code: ")
                if not api.validate_2fa_code(code):
                    logger.debug("Failed to verify (2FA) verification code")
                    sys.exit(constants.ExitCode.EXIT_FAILED_VERIFY_2FA_CODE.value)
                    
            elif api.requires_2sa:
                # fmt: off
                print("\nTwo-step (2SA) authentication required.")
                # fmt: on
                if raise_authorization_exception:
                    raise PyiCloud2SARequiredException(username)

                print("\nYour trusted devices are:")
                devices = api.trusted_devices
                for i, device in enumerate(devices):
                    print(
                        "    %s: %s"
                        % (
                            i,
                            device.get(
                                "deviceName", "SMS to %s" % device.get("phoneNumber")
                            ),
                        )
                    )

                device = int(input("\nWhich device number would you like to use: "))
                device = devices[device]
                if not api.send_verification_code(device):
                    logger.debug("Failed to send verification code")
                    sys.exit(constants.ExitCode.EXIT_FAILED_SEND_2SA_CODE)

                code = input("\nPlease enter two-step (2SA) validation code: ")
                if not api.validate_verification_code(device, code):
                    print("Failed to verify verification code")
                    sys.exit(constants.ExitCode.EXIT_FAILED_VERIFY_2FA_CODE)
            # Auth success
            logger.debug(f"Authenticated as {username}")
            return api

        except PyiCloudFailedLoginException as err:
            # If the user has a stored password; we just used it and
            # it did not work; let's delete it if there is one.
            #if utils.password_exists_in_keyring(username):
            #    utils.delete_password_in_keyring(username)

            failure_count += 1
            message = f"PyiCloudFailedLoginException for {username}, {err}, failure count {failure_count}"
            logger.info(message)
            if failure_count >= constants.AUTHENTICATION_MAX_RETRIES:
                raise PyiCloudFailedLoginException(message)


        except PyiCloudNoStoredPasswordAvailableException:
            if raise_authorization_exception:
                message = f"No stored password available for {username} and not a TTY!"
                raise PyiCloudFailedLoginException(message)

            # Prompt for password if not stored in PyiCloud's keyring
            password = click.prompt("iCloud Password", hide_input=True)
            if (
                not utils.password_exists_in_keyring(username)
                and click.confirm("Save password in keyring?")
            ):
                utils.store_password_in_keyring(username, password)

