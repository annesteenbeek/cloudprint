#!/usr/bin/env python
# Copyright 2014 Jason Michalski <armooo@armooo.net>
#
# This file is part of cloudprint.
#
# cloudprint is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# cloudprint is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with cloudprint.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import
from __future__ import print_function

import datetime
import hashlib
import json
import logging
import logging.handlers
import os
import re
import requests
import stat
import sys
import time
import uuid

import ConfigParser
import smtplib
from dateutil.relativedelta import relativedelta

from tabulate import tabulate

try:
    from cloudprint import xmpp
except Exception:
    import xmpp


SOURCE = 'Armooo-PrintProxy-1'
PRINT_CLOUD_SERVICE_ID = 'cloudprint'
CLIENT_LOGIN_URL = '/accounts/ClientLogin'
PRINT_CLOUD_URL = 'https://www.google.com/cloudprint/'



LOGGER = logging.getLogger('cloudprint')
LOGGER.setLevel(logging.INFO)

CLIENT_ID = ('607830223128-rqenc3ekjln2qi4m4ntudskhnsqn82gn'
             '.apps.googleusercontent.com')
CLIENT_KEY = 'T0azsx2lqDztSRyPHQaERJJH'


def unicode_escape(string):
    return string.encode('unicode-escape').decode('ascii')


class CloudPrintAuth(object):
    AUTH_POLL_PERIOD = 10.0

    def __init__(self, auth_path):
        self.auth_path = auth_path
        self.guid = None
        self.email = None
        self.xmpp_jid = None
        self.exp_time = None
        self.refresh_token = None
        self._access_token = None

    @property
    def session(self):
        s = requests.session()
        s.headers['X-CloudPrint-Proxy'] = 'ArmoooIsAnOEM'
        s.headers['Authorization'] = 'Bearer {0}'.format(self.access_token)
        return s

    @property
    def access_token(self):
        if datetime.datetime.now() > self.exp_time:
            self.refresh()
        return self._access_token

    def no_auth(self):
        return not os.path.exists(self.auth_path)

    def login(self, name, description, ppd):
        self.guid = str(uuid.uuid4())
        reg_data = requests.post(
            PRINT_CLOUD_URL + 'register',
            {
                'output': 'json',
                'printer': name,
                'proxy':  self.guid,
                'capabilities': ppd.encode('utf-8'),
                'defaults': ppd.encode('utf-8'),
                'status': 'OK',
                'description': description,
                'capsHash': hashlib.sha1(ppd.encode('utf-8')).hexdigest(),
            },
            headers={'X-CloudPrint-Proxy': 'ArmoooIsAnOEM'},
        ).json()
        print('Go to {0} to claim this printer'.format(
            reg_data['complete_invite_url']
        ))

        end = time.time() + int(reg_data['token_duration'])
        while time.time() < end:
            time.sleep(self.AUTH_POLL_PERIOD)
            print('trying for the win')
            poll = requests.get(
                reg_data['polling_url'] + CLIENT_ID,
                headers={'X-CloudPrint-Proxy': 'ArmoooIsAnOEM'},
            ).json()
            if poll['success']:
                break
        else:
            print('The login request timedout')

        self.xmpp_jid = poll['xmpp_jid']
        self.email = poll['user_email']
        print('Printer claimed by {0}.'.format(self.email))

        token = requests.post(
            'https://accounts.google.com/o/oauth2/token',
            data={
                'redirect_uri': 'oob',
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_KEY,
                'grant_type': 'authorization_code',
                'code': poll['authorization_code'],
            }
        ).json()

        self.refresh_token = token['refresh_token']
        self.refresh()

        self.save()

    def refresh(self):
        token = requests.post(
            'https://accounts.google.com/o/oauth2/token',
            data={
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_KEY,
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token,
            }
        ).json()
        self._access_token = token['access_token']

        slop_time = datetime.timedelta(minutes=15)
        expires_in = datetime.timedelta(seconds=token['expires_in'])
        self.exp_time = datetime.datetime.now() + (expires_in - slop_time)

    def load(self):
        if os.path.exists(self.auth_path):
            with open(self.auth_path) as auth_file:
                auth_data = json.load(auth_file)
            self.guid = auth_data['guid']
            self.xmpp_jid = auth_data['xmpp_jid']
            self.email = auth_data['email']
            self.refresh_token = auth_data['refresh_token']

        self.refresh()

    def delete(self):
        if os.path.exists(self.auth_path):
            os.unlink(self.auth_path)

    def save(self):
            if not os.path.exists(self.auth_path):
                with open(self.auth_path, 'w') as auth_file:
                    os.chmod(self.auth_path, stat.S_IRUSR | stat.S_IWUSR)
            with open(self.auth_path, 'w') as auth_file:
                json.dump({
                    'guid':  self.guid,
                    'email': self.email,
                    'xmpp_jid': self.xmpp_jid,
                    'refresh_token': self.refresh_token,
                    },
                    auth_file
                )


class CloudPrintProxy(object):

    def __init__(self, auth):
        self.auth = auth
        self.sleeptime = 0
        self.site = ''
        self.include = []
        self.exclude = []

    def get_printers(self):
        printers = self.auth.session.post(
            PRINT_CLOUD_URL + 'list',
            {
                'output': 'json',
                'proxy': self.auth.guid,
            },
        ).json()
        return [
            PrinterProxy(
                self,
                p['id'],
                re.sub('^' + self.site + '-', '', p['name'])
            )
            for p in printers['printers']
        ]

class PrinterProxy(object):
    def __init__(self, cpp, printer_id, name):
        self.cpp = cpp
        self.id = printer_id
        self.name = name

    def send_mail(self, price, sender, receivers, days):

        self.cpp.email_print_log(self.id, self.name, price, sender, receivers, days)

    def email_print_log(self, printer_id, printerName, price, sender, receivers, daysAgo):
        # Check if printer_id corresponds to printer name
        # Price: price per printed page
        # The sender of the email
        # Receiver, defaults to print users
        # freq: frequency to send mail, defaults to every 4 weeks
        #TODO store logs
        if receivers is None:
            receivers = []
        if daysAgo is None:
            daysAgo = [24]

        docs = self.auth.session.post(
            PRINT_CLOUD_URL + 'jobs',
            {
                'output': 'json',
                'printerid': printer_id,
            },
        ).json()
        jobDict = {}
        for job in docs['jobs']:
            if job["status"] == "DONE": # check if done
                user = job['ownerId']
                pages = job['numberOfPages']
                newPages = 0
                printTime = datetime.datetime.fromtimestamp(int(job['updateTime'])/1000)
                if  printTime < datetime.datetime.now()-relativedelta(days=+daysAgo): # time since last send day
                    newPages = pages
                if not user in jobDict:
                    jobDict[user] = [pages, newPages, newPages * price]
                else:
                    tmpDict = jobDict[user]
                    jobDict[user] = [tmpDict[0] + pages, tmpDict[1] + newPages,
                                     (tmpDict[1] + newPages) * price]
        userList = []
        for key, value in jobDict.iteritems():
            value.insert(0, key)
            userList.append(value)
        printTable = tabulate(userList, headers=["User", "Total pages", "New pages", "Price"])
        if receivers == []: # default to user emails
            receivers = [row[0] for row in userList]

        message = ("From: From PrintServer <" + sender + " >\n"
                    "To: To Person <" + ", ".join(receivers) + ">\n"
                    "Subject: Prints this month for printer '" + printerName + "'\n \n"
                   "These are the prints for the month: \n \n"
                )
        message += printTable
        print("Trying to send: \n" + message)
        try:
           smtpObj = smtplib.SMTP('localhost')
           smtpObj.sendmail(sender, receivers, message)
           print("Successfully sent email")
        except Exception:
           print("Error: unable to send email")


# True if printer name matches *any* of the regular expressions in regexps
def match_re(prn, regexps, empty=False):
    if len(regexps):
        try:
            return (
                re.match(regexps[0], prn, re.UNICODE)
                or match_re(prn, regexps[1:])
            )
        except Exception:
            sys.stderr.write(
                'cloudprint: invalid regular expression: ' +
                regexps[0] +
                '\n'
            )
            sys.exit(1)
    else:
        return empty

def manage_mail_logs(cups_connection, cpp):
    # every x minutes, check conf
    # if printer to track matches printer name:
    #   if day of month to print matches, send mail
    conf = ConfigParser.ConfigParser()
    while True:
        conf.read("printers.conf")
        printers = cpp.get_printers()
        try:
            for printer in printers:
                if printer.name in conf.sections():
                    opt = ConfigSectionMap(conf, printer.name)
                    # today is one of the days of the month
                    printer.send_mail(opt['price'], opt['sender'], opt['receivers'], opt['days'])
        except Exception:
            LOGGER.exception("oh snap, email exception")
        time.sleep(60)

def ConfigSectionMap(conf, section):
    dict1 = {}
    options = conf.options(section)
    for option in options:
        try:
            dict1[option] = conf.get(section, option)
            if dict1[option] == -1:
                print("skip: %s" % option)
        except:
            print("exception on %s!" % option)
            dict1[option] = None
    return dict1

def main():

    auth = CloudPrintAuth('~/.cloudprintauth.json')

    cpp = CloudPrintProxy(auth)

    auth.load()

    #manage_printers(cups_connection, cpp)
    for printer in cpp.get_printers():
        print("my printers are: " + printer.name + "\n")

if __name__ == '__main__':
    main()
