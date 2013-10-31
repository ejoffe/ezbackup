#!/usr/bin/python

import argparse
import json
import logging
import os
import errno
import smtplib
import subprocess
import time
import shutil
from datetime import datetime

CONFIG_FILE = 'config.json'
LOG_FILE = 'ezbackup.log'

rsync_flags = [ "--archive",
                "--one-file-system",
                "--hard-links",
                "--human-readable",
                "--inplace",
                "--numeric-ids",
                "--delete",
                "--delete-excluded" ]

rsync_flags_verbose = [ "--verbose",
                        "--progress",
                        "--itemize-changes" ]

# 'mkdir -p' : create the dir if it doesn't exist
def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise

def parse_args(usernames):
    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--username', help='Backup username', choices=usernames)
    parser.add_argument('-e', '--email', help='Email address to send a report upon completion')
    parser.add_argument('-d', '--dry', help='Dry run', action='store_true')
    parser.add_argument('-v', '--verbose', help='Verbose', action='store_true')
    return parser.parse_args()

def send_email(login, to, subject='', message=''):
    """login = {user, password, server, port=587}"""
    client = smtplib.SMTP('%s:%s' % (login['server'], login.get('port', 587)))
    client.starttls()
    client.login(login['user'], login['password'])
    client.sendmail(login['user'], to, 'Subject: %s\n\n%s' % (subject, message))
    client.quit()

# TODO - this function is a little unsafe
# if some random files or dirs are added to the backup dir the return of this function
# will be unpredictable 
def backup_stats( base_dir ):
    """returns = ( count, newest, oldest )"""
    dirs = os.listdir( base_dir )
    newest = ''
    oldest = 'Z'
    count = 0
    for dir in dirs:
        if dir in ( 'incomplete', 'current' ):
            continue

        count += 1
        if dir > newest:
            newest = dir
        if dir < oldest:
            oldest = dir
    return ( count, newest, oldest )

def run_rsync(config, profile, flags, path):
    source = profile['username'] + "@"
    source += profile['hostname'] + ":"
    source += path

    target = config['backup_path'] 
    target += profile['username'] + '/' 
    target += profile['hostname'] + '/'
    target += path.replace( '/', '_' ) + '/'
    base_dir = target
    target += "incomplete" + '/'
    mkdir_p( target )

    excludes = [('--exclude="%s"' % e) for e in profile.get('excludes', [])]
    flags += excludes

    # find latest and oldest backup to be used for link-dest
    ( backup_count, newest_backup, oldest_backup ) = backup_stats( base_dir )
    flags += [ '--link-dest=%s/%s' % ( base_dir, newest_backup ) ]

    command = ['rsync'] + flags + [source, target]

    logging.info('Running username:%s host:%s dir:%s' % ( profile['username'], 
                                                          profile['hostname'],
                                                          path ) )
    starttime = time.time()
    returncode = subprocess.call(command)
    endtime = time.time()
    if returncode != 0:
        logging.error('%s\nReturn code: %d' % (' '.join(command), returncode))

    if returncode == 0:
        cwd = os.getcwd()
        os.chdir( base_dir )
        # rename incomplete 
        utcnow = datetime.utcnow()
        timestr = utcnow.strftime( '%Y-%m-%d__%H-%M-%S' )
        os.rename( 'incomplete', timestr )

        # create symlink
        try:
            os.remove( 'current' )
        except:
            pass
        os.symlink( timestr, 'current' )

        # purge oldest backup
        if backup_count >= config[ 'backup_count' ]:
            junk_path = config[  'backup_path' ] + 'junk/' + oldest_backup
            os.rename( oldest_backup, junk_path )

        os.chdir( cwd )

    logging.info('Complete. Elapsed time: %0.2f seconds', endtime - starttime)
    return returncode == 0

def init_logging():
    logging.basicConfig(
        filename=LOG_FILE,
        filemode='w',
        level=logging.DEBUG,
        format='[ezbackup] %(asctime)s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')

if __name__ == '__main__':
    init_logging()
    config = json.loads(open(CONFIG_FILE).read())
    args = parse_args([p['username'] for p in config['profiles']])

    profiles = config['profiles']

    # Build list of flags.
    flags = rsync_flags
    if 'excludes' in config:
        flags += [('--exclude="%s"' % e) for e in config['excludes']]
    if args.verbose:
        flags += rsync_flags_verbose
    if args.dry:
        flags += ['-n']

    # Create junk dir
    # when old backup dirs are purged, we first move them to the junk dir
    # then at the end of the whole backup run we delete the junk dir
    # TODO - this maybe unsafe as there may be a user named 'junk'
    junkDirPath = config[ 'backup_path' ] + 'junk'
    mkdir_p( junkDirPath )

    # Now, do it!
    success = True
    for profile in profiles:
        for path in profile[ 'dirs' ]:
            success = run_rsync(config, profile, flags, path) and success

    if args.email:
        send_email(
            login=config['email'],
            to=args.email,
            subject=('[OK]' if success else '[FAIL]') + ' ezbackup complete.',
            message=open(LOG_FILE).read())

    # Delete junk dir with all the purged backups
    shutil.rmtree( junkDirPath )

