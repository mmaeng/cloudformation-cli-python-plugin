#!/usr/bin/env bash
DIR=$(mktemp -d)
cd "$DIR"
ls -la
cfn init -t AWS::Foo::Bar -a RESOURCE $1 --use-docker
ls -la
mypy src/aws_foo_bar/ --strict --implicit-reexport
cfn validate -vvv
cfn generate -vvv
# cfn submit --dry-run -vvv
DIR=$(mktemp -d)
cd "$DIR"
ls -la
cfn init -t AWS::Foo::Bar::Module -a MODULE $1 --use-docker
ls -la
cfn validate -vvv
cfn generate -vvv
# cfn submit --dry-run -vvv
DIR=$(mktemp -d)
cd "$DIR"
ls -la
cfn init -t AWS::Foo::Bar -a HOOK $1 --use-docker
ls -la
cfn validate -vvv
cfn generate -vvv
# cfn submit --dry-run -vvv
