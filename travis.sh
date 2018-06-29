#!/bin/bash

# parse options
export rebuild=true
export debug=false
export buildonly=false
export testonly=false
while [ "$#" -gt 0 ]; do
    arg=$1
    case $1 in
        -n|--no-rebuild) shift; rebuild=false;;
        -d|--debug) shift; debug=true;;
        -o|--buildonly) shift; buildonly=true;;
        -*) usage_fatal "unknown option: '$1'";;
        *) break;; # reached the list of file names
    esac
done

if [[ "${buildonly}" != true ]]; then
    if [ -n "$TRAVIS" ]; then
        echo '##### Installing Python stuff for testing.'
        if [ "$(lsb_release -s -c)" = xenial ]
        then
            pip3 install -U setuptools
        fi
        sudo pip3 install redis plumbum pyyaml
        #sudo apt-get install -y lcov
    fi
fi

set -o errexit
set -o nounset
set -o pipefail

#build Dynomite
if [[ "${rebuild}" == "true" ]]; then
    echo '##### Rebuilding Dynomite'
    # Jalb: CFLAGS was set before autoreconf and therefore its value unused. Previous value: "-ggdb3 -O0"
    # No code coverage until it works.
    #autoreconf -fvi &&  ./configure --enable-debug=log CFLAGS='--coverage -ggdb3 -O0' LDFLAGS='--coverage' && make
    autoreconf -fvi &&  ./configure --enable-debug=log && make
else
    echo '##### Not rebuilding Dynomite.'
fi

#./src/dynomite -h

# After compilation we should have as many gcno files as source files
#echo '##### Checking for post-compilation code coverage files'
#find . -name '*.gcno' -ls

if [[ "${buildonly}" == true ]]; then
    echo '##### Build only: exiting.'
    exit 0
fi

# Simple connectivity test
function simple_connectivity_test() {
    redis-server --requirePass testpass --masterauth testpass --bind 127.0.1.2 --port 1212 &
    redis-server --requirePass testpass --masterauth testpass --bind 127.0.1.3 --port 1212 &
    sleep 2
    echo 'info' | redis-cli -h 127.0.1.2 -p 1212 -a testpass 
    echo 'info' | redis-cli -h 127.0.1.3 -p 1212 -a testpass 
    mkdir logs
    ./src/dynomite -v 11 -o logs/dynomite1.log -c docker-build/dynomite1.conf &
    ./src/dynomite -v 11 -o logs/dynomite2.log -c docker-build/dynomite2.conf &
    echo 'info' | redis-cli -h 127.0.1.2 -p 8102 -a testpass 
    echo 'info' | redis-cli -h 127.0.1.3 -p 8102 -a testpass
}

echo '##### Running Dynomite.'

# Run non-auth regular tests
time ./test/cluster_generator.py

# Make logs available as GitLab-CI artifacts.
mv test_run.* test_run

#exit 0

echo '##### Running Dynomite with AUTH'

function save_artifacts_and_exit()
{
    echo "save_artifacts_and_exit: called after $1"
    #ps aux|grep redis
    mv test_run.* test_auth_run
    exit 0
}

# Run auth tests
time ./test_auth/cluster_generator.py

# Uncomment this and make the above command run in the background if the test scripts just hang
#sleep 600 && save_artifacts_and_exit alarm

# Make logs available as GitLab-CI artifacts.
save_artifacts_and_exit 'return from test'

#echo '##### Checking for code coverage data'
#ls -l src

gcov src/dynomite.c
SUBDIRS='./src/entropy ./src/event ./src/hashkit ./src/proto ./src/seedsprovider'
ls -l src $SUBDIRS

OPTIONS=''
for F in $SUBDIRS
do
    OPTIONS="${OPTIONS} -d $F"
done

lcov -d ./src $OPTIONS -c -o lcov-report.info
ls -l lcov-report.info
genhtml -o coverage lcov-report.info

