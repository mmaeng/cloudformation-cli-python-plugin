def setup_subparser(subparsers, parents, python_version, python_version_number):
    parser = subparsers.add_parser(
        python_version,
        description=(
            "This sub command generates IDE and build files for Python "
            "{}".format(python_version_number)
        ),
        parents=parents,
    )
    parser.set_defaults(language=python_version)

    group = parser.add_mutually_exclusive_group()

    group.add_argument(
        "-d",
        "--use-docker",
        action="store_true",
        help="""Use docker for python platform-independent packaging.
            This is highly recommended unless you are experienced
            with cross-platform Python packaging.""",
    )

    group.add_argument(
        "--no-docker",
        action="store_true",
        help="""Generally not recommended unless you are experienced
            with cross-platform Python packaging""",
    )

    return parser


def setup_subparser_python36(subparsers, parents):
    return setup_subparser(subparsers, parents, "python36", "3.6")


def setup_subparser_python37(subparsers, parents):
    return setup_subparser(subparsers, parents, "python37", "3.7")


def setup_subparser_python38(subparsers, parents):
    return setup_subparser(subparsers, parents, "python38", "3.8")


def setup_subparser_python39(subparsers, parents):
    return setup_subparser(subparsers, parents, "python39", "3.9")
