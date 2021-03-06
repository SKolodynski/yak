#
#  Copyright (c) 2011-2014 Exxeleron GmbH
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#

import cmd
import logging
import os
import re
import shlex
import signal
import subprocess
import sys

from datetime import datetime
from optparse import OptionParser

from osutil import get_username
from components import manager, component
from components.utils import get_full_exc_info, get_short_exc_info, to_camel_case, to_underscore


IMPRINT = {'script': 'yak', 'tstamp': '20140516100542', 'version': '3.0.0', 'name': 'yak', 'author': 'exxeleron'} ### imprint ###
ROOT_DIR = os.path.dirname(sys.path[0])
VIEWER = None
HLINE = "-" * 80



def show_file(path, internal = False):
    if not path:
        return
    elif internal or VIEWER is None:
        print "# {0}".format(os.path.normpath(path))
        if os.path.exists(path):
            with open(path, "r") as file:
                for line in file:
                    print line.rstrip()
        else:
            print "Cannot locate file: %s" % path
    else:
        p = subprocess.Popen([VIEWER, path])
        p.communicate()



class ComponentManagerShellError(Exception):
    pass



class ComponentManagerShell(cmd.Cmd):
    intro = "\n{script} {version} [{tstamp}] (c) {author}\n\nType 'help' or '?' to list available commands.".format(**IMPRINT)
    outro = "Thank you for using {script}.".format(**IMPRINT)
    prompt = ">>> "
    logger = logging.getLogger("yak")

    def __init__(self, options):
        cmd.Cmd.__init__(self)
        self._options = options
        self._manager = manager.ComponentManager(options.config, options.status)
        self._parse_format(options.format, options.delimiter)
        self._complete_names = self._manager.groups.keys() + self._manager.dependencies_order[:]

    def _parse_format(self, formating, delimiter):
        r = re.compile("\d+")
        format = [tuple(column.split(":")) for column in formating.split("#")]
        self._info_parameters = ["{0}".format(c, r.search(f).group(0)) for (c, f) in format]
        if delimiter == " ":
            self._info_header = " ".join(["{0:{1}.{1}}".format(c, r.search(f).group(0)) for (c, f) in format])
            self._info_header += "\n" + "-" * len(self._info_header)
            self._info_format = " ".join(["{{{0}:{1}}}".format(c, f) for (c, f) in format])
        else:
            self._info_header = delimiter.join(self._info_parameters)
            self._info_format = delimiter.join(["{" + p + "}" for p in self._info_parameters])

    # behavior configuration
    def postcmd(self, stop, line):
        sys.stdout.flush()
        return False

    def emptyline(self):
        return  # disable auto launching of last command

    def completenames(self, text, *ignored):
        dotext = "do_" + text
        hiddendotext = "do__" + text
        return [a[3:] for a in self.get_names() if a.startswith(dotext) and not a.startswith(hiddendotext)]

    def completedefault(self, text, *ignored):
        if text:
            return [uid for uid in self._complete_names if uid.startswith(text)]
        else:
            return self._complete_names

    def parseline(self, line):
        if line:
            if line[0] == "%":  # add special command "%"
                line = "_show_options"
            elif line[0] == "!":  # add special command "!"
                line = "_show_order"
            elif line[0] == ".":  # overload info command
                line = "info " + line[1:]
            elif line[0] == ":":  # overload details command
                line = "details " + line[1:]
            elif line == r"\\":  # overload quit command
                line = "quit"

        return cmd.Cmd.parseline(self, line)

    # decorators
    def _get_components_list_and_params(self, args):
        components = []

        params = {}
        identifiers = []
        arguments = shlex.split(args)

        idx = 0;

        while idx < len(arguments):
            if arguments[idx][0] == "-" and idx + 1 < len(arguments):
                params[arguments[idx]] = arguments[idx + 1]
                idx += 1
            else :
                identifiers.append(arguments[idx])

            idx += 1

        if len(identifiers) > 0:
            for id in identifiers:
                if id == "*" :
                    components = self._manager.dependencies_order[:]
                else :
                    ids = id.split(".")
                    if len(ids) == 1:  # namespace or group
                        if id in self._manager.groups:
                            group = self._manager.groups[id]
                        else:
                            group = [uid for uid in self._manager.dependencies_order if uid.startswith(id)]
                        if (group):
                            components.extend(group)
                        else:
                            raise ComponentManagerShellError("Trying to access unmanaged group: {0}".format(id))
                    elif len(ids) == 2:  # component
                        if (id in self._manager.dependencies_order):
                            components.append(id)
                        else:
                            raise ComponentManagerShellError("Trying to access unmanaged component: {0}".format(id))
                    else:  # error
                        raise ComponentManagerShellError("Malformed group/component identifier: {0}".format(id))

            components = [s for s in self._manager.dependencies_order if s in components]  # remove duplicates, restore runtime order
        return components, params

    def _allow_empty_components_list(f):  # @NoSelf
        def expand_to_all_components(self, args):
            if args :
                return f(self, args)
            else :
                return f(self, "*")
        return expand_to_all_components

    def _multiple_components_allowed(f):  # @NoSelf
        def multi_components_command(self, args):
            if args :
                ComponentManagerShell.logger.info("%s %s", f.func_name[3:], args, extra = {"user": get_username()})
                self._manager.reload()
                return f(self, *self._get_components_list_and_params(args))
            else :
                raise ComponentManagerShellError("Group, namespace or component id required")
        return multi_components_command

    def _single_component_allowed(f):  # @NoSelf
        def single_component_command(self, arg):
            ComponentManagerShell.logger.info("%s %s", f.func_name[3:], arg, extra = {"user": get_username()})
            components, params = self._get_components_list_and_params(arg)
            if len(components) != 1:
                raise ComponentManagerShellError("Operation can only be performed on single component")
            self._manager.reload()
            return f(self, components[0], params)
        return single_component_command

    def _error_handler(f):  # @NoSelf
        def tracked_command(self, args):
            try:
                return f(self, args)
            except:
                print get_short_exc_info()
                ComponentManagerShell.logger.error(get_full_exc_info(), extra = {"user": get_username()})
                return 1
        return tracked_command

    # utility functions
    def _apply_command(self, command, components):
        failed = []
        for component_uid in components:
            try:
                status = command(component_uid)
                print "\t{0:<30}\t{1}".format(component_uid, "OK" if status else "Skipped")
            except:
                failed.append(component_uid)
                print "\t{0:<30}\tFailed: {1}".format(component_uid, get_short_exc_info())
                ComponentManagerShell.logger.error(get_full_exc_info(), extra = {"user": get_username()})

        if failed:
            for component_uid in failed:
                print HLINE
                print "stderr for component: {0}".format(component_uid)
                show_file(self._manager.components[component_uid].stderr, True)

            print HLINE
            return 1

    # utility function
    def _apply_params_to_config(self, params, components):
        for component_uid in components:
            if component_uid in self._manager.components:
                component_cfg = self._manager.components[component_uid].configuration
                if ("-a" in params) or ("--arguments" in params):
                    component_cfg.command_args = params["-a"]

    def _format_parameter(self, key, value, default = ""):
        if isinstance(value, (list, tuple, set)):
            return ", ".join(str(e) for e in value)
        elif isinstance(value, datetime):
            return value.strftime('%Y.%m.%d %H:%M:%S')
        elif isinstance(value, (int, long)):
            return str(value)
        elif isinstance(value, float):
            return "{0:.3f}".format(value)
        elif key.find("password") >= 0:
            return "***"
        elif not value:
            return default

        return value

    # shell commands
    def do__show_options(self, args):
        ENTRY_FORMAT = "{0:>20}   {1}"
        for k, v in self._options.__dict__.iteritems():
            if v:
                print ENTRY_FORMAT.format(k , v)

    def do__show_order(self, args):
        print "\n".join(self._manager.dependencies_order)

    def do_help(self, args):
        print "Commands reference list:"
        for c, ch in COMMANDS:
            print "  %-10s %s" % (c, ch)
        print "  %-10s %s" % ("quit", "exits the interactive shell")
        print ""
        print "  %-10s %s" % ("-a", "allows start/restart process with extra arguments")

    def do_quit(self, args):
        print self.outro
        sys.exit(0)

    @_error_handler
    @_allow_empty_components_list
    @_multiple_components_allowed
    def do_info(self, components, params):
        print self._info_header
        for component_uid in sorted(components):
            parameters = dict()
            component = self._manager.components[component_uid]
            for attr in self._info_parameters:
                parameters[attr] = self._format_parameter(attr, getattr(component, to_underscore(attr).lower(), ""), "")
            print self._info_format.format(**parameters)

    @_error_handler
    @_multiple_components_allowed
    def do_details(self, components, params):
        print HLINE
        for component_uid in sorted(components):
            component = self._manager.components[component_uid]
            config = self._manager.configuration[component_uid]
            print "Component: {0}".format(component_uid)
            for attr in component.attrs:
                print "\t{0:20}\t{1}".format(to_camel_case(attr), self._format_parameter(attr, getattr(component, attr)))
            print "\nConfiguration:"
            for attr in config.attrs:
                print "\t{0:20}\t{1}".format(to_camel_case(attr), self._format_parameter(attr, getattr(config, attr)))
            print HLINE

    @_error_handler
    @_multiple_components_allowed
    def do_start(self, components, params):
        print "Starting components..."
        self._apply_params_to_config(params, components)
        return self._apply_command(self._manager.start, components)

    @_error_handler
    @_multiple_components_allowed
    def do_stop(self, components, params):
        print "Stopping components..."
        return self._apply_command(self._manager.stop, components)

    def do_restart(self, args):
        retval = self.do_stop(args)
        return retval if retval else self.do_start(args)

    @_error_handler
    @_single_component_allowed
    def do_console(self, component, params):
        print "Starting interactive console..."
        self._apply_params_to_config(params, [component])
        return self._manager.console(component)

    @_error_handler
    @_multiple_components_allowed
    def do_interrupt(self, components, params):
        print "Interrupting components..."
        return self._apply_command(self._manager.interrupt, components)

    @_error_handler
    @_single_component_allowed
    def do_out(self, component, params):
        return show_file(self._manager.components[component].stdout)

    @_error_handler
    @_single_component_allowed
    def do_err(self, component, params):
        return show_file(self._manager.components[component].stderr)

    @_error_handler
    @_single_component_allowed
    def do_log(self, component, params):
        return show_file(self._manager.components[component].log)


# option parser configuration
COMMANDS = (("start", "start component or components group"),
            ("stop", "stop component or components group"),
            ("restart", "restart component or components group"),
            ("interrupt", "send INT signal to component or components group"),
            ("info", "display status of component or components group"),
            ("details", "display detailed information on component or components group"),
            ("log", "show single component logfile content"),
            ("out", "show single component stdout"),
            ("err", "show single component stderr"),
            ("console", "start single component in interactive mode"),
            )

USAGE = "Usage: %prog [COMMAND] [COMPONENT|GROUP] [OPTIONS]\n\nCommands:\n"\
        + "\n".join(["  {0:>10}   {1}".format(c, ch) for c, ch in COMMANDS])

# bootstrap functions
def get_opt_parser():
    opt_parser = OptionParser(usage = USAGE)
    opt_parser.add_option("-c", "--config", help = "configuration [default: %default]", default = os.path.join(ROOT_DIR, "yak.cfg"))
    opt_parser.add_option("-s", "--status", help = "components status file [default: %default]", default = os.path.join(ROOT_DIR, "yak.status"))
    opt_parser.add_option("-l", "--log", help = "operations log [default: %default]", default = os.path.join(ROOT_DIR, "yak-%Y.%m.%d.log"))
    opt_parser.add_option("-v", "--viewer", help = "external viewer")
    opt_parser.add_option("-d", "--delimiter", help = "column delimiter for the info command [default: padded spaces]", default = " ")
    opt_parser.add_option("-f", "--format", help = "display format for info command", default = "uid:18#pid:5#port:6#status:11#started:19#stopped:19")
    opt_parser.add_option("-a", "--arguments", help = "additional arguments passed to process - valid only for 'start', 'restart' and 'console' commands", default = "")
    return opt_parser


def load_history(history_file):
    try:
        import readline
        import atexit
        if os.path.exists(history_file):
            readline.read_history_file(history_file)
        atexit.register(save_history, history_file)
    except ImportError:
        pass


def save_history(history_file):
    try:
        import readline
        readline.write_history_file(history_file)
    except ImportError:
        pass


def init_logging(logfile):
    logpath = os.path.split(logfile)[0]
    if not os.path.exists(logpath):
        os.makedirs(logpath)

    logging.basicConfig(filename = datetime.now().strftime(logfile),
                        level = logging.DEBUG,
                        format = "%(levelname)-7s [pid: %(process)d] %(asctime)-15s %(user)s - %(message)s",
                        )


if __name__ == "__main__":
    opts = shlex.split(os.environ.get("YAK_OPTS", ""), posix = False)
    opts.extend(sys.argv[1:])

    opt_parser = get_opt_parser()
    (options, args) = opt_parser.parse_args(args = opts)
    init_logging(options.log)
    load_history(os.path.expanduser("~/.yak.history"))
    VIEWER = options.viewer
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # ignore keyboard interrupt
    try:
        shell = ComponentManagerShell(options)
        if len(args) == 0:
            exit_status = shell.cmdloop()
        elif len(args) >= 1:
            exit_status = shell.onecmd(" ".join(args) + (" -a \"" + options.arguments + "\""  if options.arguments else ""))
    except (SystemExit, KeyboardInterrupt):
        exit_status = 0
    except:
        exit_status = get_full_exc_info()
    finally:
        logging.shutdown()
        sys.exit(exit_status)
