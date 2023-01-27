#!/usr/bin/env python3
# notifwd for macOS
# Copyright Jordan Mann,
# with credit to contributors on GitHub:
# https://github.com/jrmann100/notifwd/pulls

__version__ = "0.5"

import subprocess, sqlite3
from datetime import datetime
import plistlib
import sched, time
import requests
from sys import argv, maxsize, stdout
import argparse
from os import environ
from itertools import cycle
import pyfiglet

# I have been writing a lot of Java and am probably not supposed to
# put everything into one class like this.
class Notification:
    @staticmethod
    def parse_args(argv):
        parser = argparse.ArgumentParser(
            description="notifwd v%s - macOS notification forwarder" % __version__,
            prog="notifwd")
        
        parser.add_argument("--provider", "-p", help=f"The push-notification plugin to forward notifications to. Current options are {[k for k, _ in PROVIDERS.items()]}. Is also set using the `{APP_NAME.upper()}_PROVIDER` environment variable", 
                            default=environ.get(f"{APP_NAME.upper()}_PROVIDER"))

        parser.add_argument("--api-key", "-k",
                            help="API key for sending push notifications. Is also set using the `{APP_NAME.upper()}_API_KEY` environment variable", ",
                            default=environ.get(f"{APP_NAME.upper()}_API_KEY "))

        parser.add_argument("--user-key", "-u",
                            help="Unique user key for sending push notifications. Is also set using the `{APP_NAME.upper()}_USER_KEY` environment variable", ",
                            default=environ.get(f"{APP_NAME.upper()}_USER_KEY"))

        parser.add_argument("--frequency", "-f", type=int,
                            help="Frequency, in seconds, to check for new notifications.",
                            default=60)
        parser.add_argument("--version", action="store_true",
                            help="Get program version")

        parser.add_argument("--silent", "-s",
                            help="Don't display the splash screen or verbose logging.", action="store_true")
                            
        parser.add_argument("--test", "-t",
                            help="Display a test notification on startup.", action="store_true")
        return parser.parse_args()

    @staticmethod
    def validate_args(args):
        if args.provider == None or type(args.provider) is not str:
            args.provider = "default"
        
        args.provider = args.provider.lower()

        if PROVIDERS.get(args.provider) == None:
            args.provider = "default"
        
        if args.frequency <= 0:
            raise Exception("frequency must be a positive integer.")
        
        PROVIDERS[args.provider].validate_args(args)
        
    @staticmethod
    def setup(argv):
        # Parse the command-line arguments.
        args = Notification.parse_args(argv)

        if args.version:
            print("notifwd v%s" % __version__)
            raise SystemExit()
        
        # Verify arguments
        err = Notification.validate_args(args)
        if err != None:
            exit(err)

        # Set notification values
        Notification.FREQ = args.frequency
        Notification.SILENT = args.silent
        Notification.TEST = args.test

        # Configure provider
        Notification.PROVIDER = PROVIDERS[args.provider](args)

        if not Notification.SILENT:
            print(pyfiglet.figlet_format(APP_NAME))
            print(f"Using provider {args.provider}")

        # Get the system temp directory macOS is caching to.
        tmp_path = subprocess.run(["getconf", "DARWIN_USER_DIR"], stdout=subprocess.PIPE).stdout
        # Locate the database; start SQLite.
        db_path = tmp_path.decode("utf-8").rstrip() + "com.apple.NotificationCenter/db2/db"
        db_path_fails = subprocess.run(["stat", db_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if (db_path_fails):
            db_path = tmp_path.decode("utf-8").rstrip() + "com.apple.notificationcenter/db2/db"
        Notification.connection = sqlite3.connect(db_path)
        Notification.cursor = Notification.connection.cursor()
        # Set the most recent notification ID to the ID of the last-displayed notification.
        last_data = Notification.get_notification_data(0)
        if last_data:
            Notification.last_id = last_data[0]
            Notification.last_date = last_data[6]
        if Notification.TEST:
            print("Sending test notification... ", end="")
            subprocess.run(["osascript", "-e", "display notification time string of (current date) with title \"The time is\" subtitle \"Most definitely\""])
        if not Notification.SILENT: print("done.")

    @staticmethod
    def main(argv):
        Notification.setup(argv)
        s = sched.scheduler(time.time, time.sleep)
        #https://stackoverflow.com/a/22616059/9068081
        spinner = cycle(['*','-', '/', '|', '\\','-','*'])
        def scheduled_update(s):
            if not Notification.SILENT:
                    for i in range(0,7):
                        time.sleep(0.1)
                        stdout.write(next(spinner))
                        stdout.flush()
                        stdout.write('\b')

            Notification.check()
            # Schedule to run periodically.
            s.enter(Notification.FREQ - 0.7, 1, scheduled_update, (s,))
        # Schedule to run on start.
        s.enter(0, 1, scheduled_update, (s,))
        try:
            print("Starting scheduler. Update frequency is %d second%s. " % (Notification.FREQ, ("s" if Notification.FREQ != 1 else "")), end="")
            stdout.flush() # See note above.
            s.run()
        except KeyboardInterrupt:
            print("\nQuitting...")
            Notification.connection.close()
            raise SystemExit # Equivalent to quit() or exit()
        except Exception as e:
            raise(e)

    # Create current Cocoa Core Data Timestamp (seconds since Jan 1 2001)
    # and subtract notification date to find how many seconds ago it was.
    # https://www.epochconverter.com/coredata
    @staticmethod
    def coredata_now():
        return (datetime.utcnow() - datetime(2001,1,1)).total_seconds()

    # Fetch data for a specific notification from the database.
    @staticmethod
    def get_notification_data(n):
        #return Notification.cursor.execute("SELECT *, NTH_VALUE(rec_id,%d) OVER (ORDER BY rec_id DESC) FROM record LIMIT 1" % (n + 1)).fetchone()
        # I know there is a better way to do this, but I've spent an hour with my limited SQLite knowledge and it isn't enough.
        return Notification.cursor.execute("SELECT * FROM (SELECT * FROM record ORDER BY rec_id DESC LIMIT %d) ORDER BY rec_id LIMIT 1" % (n + 1)).fetchone()
    
    # Get an application name like "Messages" from an identifier like "com.apple.Messages"
    # that comes with the notification.
    @staticmethod
    def lookup_display_name(identifier):
        return subprocess.run(["mdfind", "kMDItemCFBundleIdentifier", "=",
                               identifier.strip(), "-attr", "kMDItemDisplayName"],
                              stdout=subprocess.PIPE).stdout.decode("utf-8").split(" = ")[-1].strip()

    # Inititialize nonstatic Notification attributes.
    def __init__(self):
        self.identifier = ""
        self.app = ""
        self.title = ""
        self.subtitle = ""
        self.body = ""
        # Combined body and subtitle.
        self.text = ""
        self.ago = 0
        self.date = 0
        self.xml = ""

    # Display notification info, for logging.
    def __str__(self):
        return ("%d minutes ago from %s: \"%s\"" % (
            (int(self.ago/60)), self.app, self.title.strip()))

    # Collect recent notifications.
    @staticmethod
    def check():
        # Oh, I've figured it out. We need to cross-check by timestamps, or dismissed notifications cause the system to never encounter into last_id.
        n = 0
        sql_data = Notification.get_notification_data(n)
        if sql_data:
            newest_id = sql_data[0]
            # Either delivered_date or request_date will be filled in. Don't yet want to peek into what those mean.
            newest_date = (sql_data[6] if sql_data[6] != None else sql_data[4])
            # print("DEBUG", (sql_data[6] if sql_data[6] != None else sql_data[4]), Notification.last_date)
            while sql_data[0] != Notification.last_id and (sql_data[6] if sql_data[6] != None else sql_data[4]) >= Notification.last_date:
                # print("N is ", n, "last id", Notification.last_id, "newest id", newest_id, "this id", sql_data[0])
                Notification.send(Notification.parse_notification(sql_data[3]))
                n += 1
                sql_data = Notification.get_notification_data(n)
            Notification.last_id = newest_id
            Notification.last_date = newest_date

    # Create a notification from raw plist data. The returned notification can then be sent.
    @staticmethod
    def parse_notification(raw_plist):
        this = Notification()
        # Parse raw database data, which is an Apple plist.
        data = plistlib.loads(raw_plist)
        for key, value in data.items():
            if key == "app":
                this.identifier = value or ""
                this.app = Notification.lookup_display_name(value) or ""
            elif key == "date":
                this.date = float(value)
                this.ago = Notification.coredata_now() - float(value)
            elif key == "req":
                for subkey, subvalue in value.items():
                    if subkey == "titl":
                        this.title = subvalue or ""
                    if subkey == "subt":
                        this.subtitle = subvalue or ""
                    if subkey == "body":
                        this.body = subvalue or ""
        # Merge subtitle and body - yes, notifications have three lines.
        this.text = this.subtitle + ("\u2014" if this.subtitle else "") + this.body
        return this

    # Send a notification to the Prowl API.
    def send(self):
        if not Notification.SILENT: print("\nSending notification from", self)
        
        response = Notification.PROVIDER.send_notification(app=self.app, title=self.title, text=self.text)

        if response.status_code != 200:
            print("Received unexpected status code", response.status_code, response.reason, "response:\n", response.text)


# List of providers
class PushOver():
    def __init__(self, args):
        self.url_endpoint = "https://api.pushover.net/1/messages.json"
        self.api_key = args.api_key
        self.user_key = args.user_key

    
    def send_notification(self, app, title, text):
        resp = requests.post(self.url_endpoint,
                            data={  "token": self.api_key,
                                    "user": self.user_key,
                                    "message": f"{app}: {title} \n {text}",
                        } )

        return resp
    
    # TODO Move adding args to parsers in the Provider, making the plugin fully independent
    @staticmethod
    def validate_args(args):
        if args.api_key is None:
            raise Exception(f"no API key specified. Is ${APP_NAME.upper()}_API_KEY defined?")
        if args.user_key is None:
            raise Exception(f"no USER key specified. Is ${APP_NAME.upper()}_USER_KEY defined?")

                        
class Prowl:
    def __init__(self, args):
        self.url_endpoint = "https://api.prowlapp.com/publicapi/add"
        self.api_key = args.api_key


    def send_notification(self, app, title, text):
        resp = requests.post(self.url_endpoint,
            data={"apikey": self.api_key, "application": app,
                "event": title, "description": text})

        return resp
    
    # TODO Move adding args to parsers in the Provider, making the plugin fully independent
    @staticmethod
    def validate_args(args):
        if args.api_key is None:
            raise Exception(f"no API key specified. Is ${APP_NAME.upper()}_API_KEY defined?")


# Project-wide values
APP_NAME="notifwd"
PROVIDERS={"prowl": Prowl,
            "pushover": PushOver,
            "default": Prowl}

if __name__ == "__main__":
    Notification.main(argv)
