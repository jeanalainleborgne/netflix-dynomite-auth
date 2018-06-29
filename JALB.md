# Notes on merging https://github.com/lampmanyao/dynomite into Netflix's version

Author: Jean-Alain Le Borgne

We are using GitLab-CI, so a .gitlab-ci.yml was added that simulates Travis.

The travis.sh script contains unfinished code coverage instructions.

The test_auth directory is a copy of the test directory, modified with
AUTH test cases.

The docker-build directory is an unfinished attempt at running the
tests in a local Docker container, to work in a tighter cycle than the
commit-push GitLab-CI cycle.
