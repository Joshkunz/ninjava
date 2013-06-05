import textwrap

import os
from os import path
import re
import functools
from pprint import pprint

""" The writer taken from the ninja_syntax.py utility """
def escape_path(word):
    return word.replace('$ ','$$ ').replace(' ','$ ').replace(':', '$:')

class Writer(object):
    def __init__(self, output, width=78):
        self.output = output
        self.width = width

    def newline(self):
        self.output.write('\n')

    def comment(self, text):
        for line in textwrap.wrap(text, self.width - 2):
            self.output.write('# ' + line + '\n')

    def variable(self, key, value, indent=0):
        if value is None:
            return
        if isinstance(value, list):
            value = ' '.join(filter(None, value))  # Filter out empty strings.
        self._line('%s = %s' % (key, value), indent)

    def pool(self, name, depth):
        self._line('pool %s' % name)
        self.variable('depth', depth, indent=1)

    def rule(self, name, command, description=None, depfile=None,
             generator=False, pool=None, restat=False, rspfile=None,
             rspfile_content=None, deps=None):
        self._line('rule %s' % name)
        self.variable('command', command, indent=1)
        if description:
            self.variable('description', description, indent=1)
        if depfile:
            self.variable('depfile', depfile, indent=1)
        if generator:
            self.variable('generator', '1', indent=1)
        if pool:
            self.variable('pool', pool, indent=1)
        if restat:
            self.variable('restat', '1', indent=1)
        if rspfile:
            self.variable('rspfile', rspfile, indent=1)
        if rspfile_content:
            self.variable('rspfile_content', rspfile_content, indent=1)
        if deps:
            self.variable('deps', deps, indent=1)

    def build(self, outputs, rule, inputs=None, implicit=None, order_only=None,
              variables=None):
        outputs = self._as_list(outputs)
        all_inputs = self._as_list(inputs)[:]
        out_outputs = list(map(escape_path, outputs))
        all_inputs = list(map(escape_path, all_inputs))

        if implicit:
            implicit = map(escape_path, self._as_list(implicit))
            all_inputs.append('|')
            all_inputs.extend(implicit)
        if order_only:
            order_only = map(escape_path, self._as_list(order_only))
            all_inputs.append('||')
            all_inputs.extend(order_only)

        self._line('build %s: %s' % (' '.join(out_outputs),
                                        ' '.join([rule] + all_inputs)))

        if variables:
            if isinstance(variables, dict):
                iterator = iter(variables.items())
            else:
                iterator = iter(variables)

            for key, val in iterator:
                self.variable(key, val, indent=1)

        return outputs

    def include(self, path):
        self._line('include %s' % path)

    def subninja(self, path):
        self._line('subninja %s' % path)

    def default(self, paths):
        self._line('default %s' % ' '.join(self._as_list(paths)))

    def _count_dollars_before_index(self, s, i):
      """Returns the number of '$' characters right in front of s[i]."""
      dollar_count = 0
      dollar_index = i - 1
      while dollar_index > 0 and s[dollar_index] == '$':
        dollar_count += 1
        dollar_index -= 1
      return dollar_count

    def _line(self, text, indent=0):
        """Write 'text' word-wrapped at self.width characters."""
        leading_space = '  ' * indent
        while len(leading_space) + len(text) > self.width:
            # The text is too wide; wrap if possible.

            # Find the rightmost space that would obey our width constraint and
            # that's not an escaped space.
            available_space = self.width - len(leading_space) - len(' $')
            space = available_space
            while True:
              space = text.rfind(' ', 0, space)
              if space < 0 or \
                 self._count_dollars_before_index(text, space) % 2 == 0:
                break

            if space < 0:
                # No such space; just use the first unescaped space we can find.
                space = available_space - 1
                while True:
                  space = text.find(' ', space + 1)
                  if space < 0 or \
                     self._count_dollars_before_index(text, space) % 2 == 0:
                    break
            if space < 0:
                # Give up on breaking.
                break

            self.output.write(leading_space + text[0:space] + ' $\n')
            text = text[space+1:]

            # Subsequent lines are continuations, so indent them.
            leading_space = '  ' * (indent+2)

        self.output.write(leading_space + text + '\n')

    def _as_list(self, input):
        if input is None:
            return []
        if isinstance(input, list):
            return input
        return [input]


def escape(string):
    """Escape a string such that it can be embedded into a Ninja file without
    further interpretation."""
    assert '\n' not in string, 'Ninja syntax does not allow newlines'
    # We only have one special metacharacter: '$'.
    return string.replace('$', '$$')

# Start of Ninjava class files

##### External Dependency Resolution #####

class Ivy(object):

    def __init__(self, config, cache=None, flags=None):
        self.config = config
        if cache is not None:
            self.cache = cache
        if flags:
            self.flags = flags

    def _command(self):
        return "ivy {flags} -settings $ivy_settings {cache} -types jar " \
               "-dependency $org $artifact $version 1>/dev/null"\
               .format(flags="$ivy_flags" if hasattr(self, "flags") else "",
                       cache="-cache $ivy_cache" if hasattr(self, "cache") else "")

    def pools(self, writer):
        return [("dep_pool", 1)]

    def configs(self, writer):
        confs = {"ivy_settings": self.config}
        if hasattr(self, "cache"): confs["ivy_cache"] = self.cache
        if hasattr(self, "flags"): confs["ivy_flags"] = self.flags
        return confs

    def rules(self, writer):
        return [("ivy", {
            "command": self._command(),
            "pool": "dep_pool",
            "description": "IVY $org $artifact $version"
        })]

class Mvn(object):
    """Object representing a dependency that can be resolved
    with a remote maven repository"""

    def __init__(self, ivy, org, *artifacts, **kwargs):
        self.org = org
        self.ivy = ivy

        match = kwargs.get("match", [])
        # Get the matchers
        if isinstance(match, str):
            self.re_matchers = [re.compile(match)]
        else:
            self.re_matchers = [re.compile(m) for m in match]

        # get the artifacts 
        self.artifacts = []
        for artifact in artifacts:
            self.add_artifact(*artifact)

    def matcher(self, class_name):
        "Match imports to dependencies"
        for re_matcher in self.re_matchers:
            if re_matcher.match(class_name) is not None:
                return PathMatch(self.files(), self.files())

    def file_from_artifact(self, artifact):
        art_path = path.join(self.org, "{artifact}-{version}.jar"\
                                        .format(artifact=artifact[0], 
                                                version=artifact[1]))

        # Prepend the cache if there is one
        if hasattr(self.ivy, "cache"):
            return path.join(self.ivy.cache, art_path)

        return art_parth

    def files(self):
        for filename, spec in self.file_specs():
            yield filename

    def file_specs(self):
        for artifact in self.artifacts:
            yield (self.file_from_artifact(artifact),
                   (self.org, artifact[0], artifact[1]))

    def add_artifact(self, artifact_id, version):
        self.artifacts.append((artifact_id, version))

##### Source File Dependencies #####

class PathMatch(object):
    """An object returned from a match that represents both dependencies and
    some class path components"""

    def __init__(self, paths, deps):
        self.paths = paths
        self.depends = deps

class JavaSource(object):
    "A Bunch of java source_files"
    javac_added = False

    @classmethod
    def at(cls, path):
        "Build a project from a path"
        source = cls("")
        for dirpath, dirpaths, dirfiles in os.walk(path):
            for file in dirfiles:
                if file[0] == ".": continue
                source.add_sources(JavaSourceFile(path, 
                                                  os.path.join(dirpath, file)))
        return source

    def add_sources(self, *sources):
        "Add a new source file to the source"
        for source in sources:
            self.sources[source.class_full] = source

    def add_dependencies(self, *deps):
        "Add new dependencies to the source"
        self.dependencies.extend(deps)
        self.matchers.extend([x.matcher for x in deps])

    def __init__(self, build_path):
        self.build_path = build_path
        self.main_class = ""
        self.java_flags = ""

        self.sources = {}
        self.dependencies = []
        self.matchers = [self.com_matcher]

    def com_matcher(self, class_name):
        "Match 'com' imports and package info"
        if class_name in self.sources:
            return PathMatch(self.build_path,
                path.join(self.build_path, 
                          JavaSourceFile.pathify(class_name, ext="class")))


    def visit_dependent(self, current, first, codep, visited):
        for dependent in current.imported:
            # Skip external sources and found codependencies
            if dependent not in self.sources: continue
            # If we find a new dependency, add it, and all of its dependencies
            if dependent == first and current.class_full not in codep: 
                codep.append(current.class_full)
                self.visit_dependent(current, current.class_full, codep, [])
            if dependent not in visited:
                visited.append(dependent)
                self.visit_dependent(self.sources[dependent], first, codep, visited)

    def find_codep(self, cls):
        "Find the set of classes codependent on this class"
        codeps = [cls]
        # Find all of this class's codependents
        self.visit_dependent(self.sources[cls], cls, codeps, [])
        # return a set of them
        return set([cls] + codeps)

    def iter_build_units(self):
        found_units = []
        class_lookup = set(self.sources.keys())
        # While there are classes that have not been put into a unit
        while class_lookup != set():
            checking = class_lookup.pop()
            # Find some class's codependents
            codeps = self.find_codep(checking)
            # Remove them from the lookup set
            class_lookup -= codeps
            # Add them to the list of units
            found_units.append(codeps)

        for unit in found_units:
            output_classes = map(self.sources.get, unit)
            outputs = map(lambda x: x.file_at_prefix(self.build_path, ext="class"), 
                          output_classes)
            inputs = [x.file for x in output_classes]

            # Find the classes required to build this unit
            class_path = set()
            sources = set()
            for output_class in output_classes:
                tcp, tsp = output_class.dependents(matchers=self.matchers)
                class_path |= tcp
                sources |= tsp

            # remove any class files that will be generated with the command
            implicit = list(sources - set(outputs))
            yield outputs, "javac", {
                "inputs": inputs,
                "implicit": implicit,
                "variables": {"classpath": ":".join(class_path)}
            }

    def configs(self, writer):
        return {"java_builddir": self.build_path}

    def rules(self, writer):
        if JavaSource.javac_added: return []

        return \
        [("javac", {
            "command": "CLASSPATH=$classpath javac -d $java_builddir $in",
            "description": "JAVAC $in"
        }), ("java", {
            "command": "CLASSPATH=$classpath java $main $flags",
            "description": "JAVA $main"
        })]

    def all_dependency_files(self):
        all_deps = []
        for dependency in self.dependencies:
            all_deps.extend(dependency.files())
        return all_deps

    def all_class_files(self):
        return map(lambda x: x.file_at_prefix(self.build_path, ext="class"),
                   self.sources.itervalues())

    def builds(self, writer):

        writer.header("#### Phony Builds ####")
        # Rule to run the project once it has been built
        yield "run", "java", { "implicit": "compile", "variables": {
            "main": self.main_class,
            "classpath": ":".join([self.build_path] + self.all_dependency_files()),
            "flags": self.java_flags
        }}
        # Alias to fetch all of the dependencies
        yield "fetch-deps", "phony", { "implicit": self.all_dependency_files() }

        # Alias to compile all class files
        yield "compile", "phony", { "implicit": self.all_class_files() }

        writer.newline()

        writer.header("#### External Dependencies ####")
        for dep in self.dependencies:
            for filename, (org, artifact, version) in dep.file_specs():
                yield filename, "ivy", {
                    "variables": {
                        "org": org,
                        "artifact": artifact,
                        "version": version
                    }
                }

        writer.newline()
        writer.header("#### Internal Dependencies ####")

        for unit in self.iter_build_units():
            yield unit

        writer.newline()

class JavaSourceFile(object):
    # Match import statements
    IMPORT_MATCHER = re.compile(r"import\s+([*.a-zA-Z0-9]*)\s*;")
    # Match some provided tokens
    TOKEN_MATCHER = r"\b{matches}\b"

    # Class Utilities 
    @classmethod
    def de_prefix(cls, prefix, path):
        "remove some prefix from a path"
        return path[len(prefix):]

    @classmethod
    def classify(cls, path):
        "Turn a path in to a dotted class description"
        if path[0] == os.sep: path = path[1:]
        path = path.rsplit(".", 1)[0]
        return path.replace("/", ".")

    @classmethod
    def pathify(cls, _class, ext="java"):
        "Turn a dotted class description in to path"
        return "{path}.{ext}".format(path=_class.replace(".", "/"), ext=ext)

    @classmethod
    def _is_not_hiddenfile(cls, prefix):
        def is_not_hidden_wrapper(file):
            if file[0] == ".": return False
            return path.isfile(path.join(prefix, file))
        return is_not_hidden_wrapper

    def _fetch_package(self):
        head, tail = path.split(self.file)
        # find the class files in a directory
        package = filter(self._is_not_hiddenfile(head), os.listdir(head))
        # Get relative paths for the files
        package = map(lambda x: path.join(head, x), package)
        # De prefix the paths
        package = map(lambda x: self.de_prefix(self.source, x), package)
        # turn the file names into class names
        package = map(self.classify, package)
        # get the names of the classes (without the package prefix)
        package = map(lambda x: x.rsplit(".", 1)[1], package)
        # Remove ourself from the list
        package.remove(self.class_min)
        return package

    def _imported_classes(self):
        "Fetch the names of classes that are imported"
        classfile = open(self.file, "r")
        for match in self.IMPORT_MATCHER.finditer(classfile.read()):
            yield match.group(1)
        classfile.close()

    def _implicit_classes(self):
        "Fetch the full class names of the classes used implicity"
        pclasses = self._fetch_package()
        # If there are no classes in this package (besides this souce file),
        # there will be no implied classes required
        if not pclasses: return
        # sort the classes by length (with the largest class first)
        # so that if classes are name with prefixes, the most accurate class
        # will match
        pclasses.sort(key=lambda x: len(x), reverse=True)
        classmatch = "|".join("({0})".format(name) for name in pclasses)
        matcher = re.compile(self.TOKEN_MATCHER.format(matches=classmatch))
        with open(self.file, 'r') as f:
            for match in matcher.finditer(f.read()):
                yield ".".join((self.package, match.group(0)))


    def _java_matcher(self, class_name):
        "Match java-internal dependencies"
        if class_name.find("java") == 0:
            return False

    def _default_error(self, class_name):
        "Default match, throws an error"
        raise ValueError("No match in class {0} found for: {1}".format(
                            self.class_full, class_name))

    def _nomangle_set(self, value):
        if isinstance(value, str):
            return set([value])
        else:
            return set(value)
    
    def _handle_match(self, _class, matchers):
        for func in matchers:
            ret = func(_class)
            # If none is returned keep going
            if ret is None: pass
            # If a false value is returned, drop the dependency
            elif not ret: return set(), set()
            # If a PathMatch is found, add it
            elif isinstance(ret, PathMatch):
                return (self._nomangle_set(ret.paths),
                        self._nomangle_set(ret.depends))
            # If a normal match is found, add it
            else: 
                return set(), self._nomangle_set(ret)

    def dependents(self, matchers=[], cache=True):
        """Iterate over a list of dependent classes

        The matchers list is a list of functions that will be called
        with the name of a dependent class as their first argument. Matchers
        are called in-order, if the matcher returns 'None' the next matcher
        is tried. If a matcher returns a falsy-value, then the dependency is
        dropped.
        
        If not matchers are matched, and the dependency is not dropped, a
        ValueError is raised."""

        if cache and self.dependent_cache: return self.dependent_cache

        matchers.append(self._java_matcher)
        matchers.append(self._default_error)

        class_path = set()
        depends_list = set()

        # Handle all the matchers
        for depends in self.imported:
            class_set, depends_set = self._handle_match(depends, matchers)
            class_path |= class_set
            depends_list |= depends_set

        # Cache the dependents for these matchers
        self.dependent_cache = (class_path, depends_list)

        return class_path, depends_list

    def file_at_prefix(self, prefix, ext="class"):
        return path.join(prefix, self.pathify(self.class_full, ext=ext))

    def __init__(self, source_path, file_path):
        self.source = source_path
        self.file = file_path
        self.class_full = self.classify(self.de_prefix(self.source, self.file))
        self.package, self.class_min = self.class_full.rsplit(".", 1)

        # Get a list of classes imported by this file
        self.imported = set(self._imported_classes())
        # Add the list of implicity used clases to the list of imported classes
        self.imported = self.imported | set(self._implicit_classes())

        self.dependent_cache = []

def obj_hasattr(*attrs):
    def obj_hasattr_decorator(func):

        @functools.wraps(func)
        def wrapper(self, obj, *args, **kwargs):
            if all(map(lambda x: hasattr(obj, x), attrs)):
                return func(self, obj, *args, **kwargs)

        return wrapper

    return obj_hasattr_decorator

class Ninjava(ninja_syntax.Writer):
    "A ninja file writer"

    def __init__(self, buildfile_name):
        self.buildfile = open(buildfile_name, 'w')
        super(Ninjava, self).__init__(self.buildfile)

        self.objs = []

    def add(self, *objs):
        self.objs.extend(objs)

    def header(self, text):
        self._line(text)
        self.newline()

    def _any_have(self, *attrs):
        "Check to see if any of our objects have the requisite attributes"
        return any(all(map(lambda x: hasattr(obj, x), attrs)) \
                    for obj in self.objs)
    def flush(self):
        "Write all currently buffered config elements"

        # shortcut if nothing to do
        if not self.objs: return

        if self._any_have("configs"):
            self.header("#### Configurations ####")
            for obj in self.objs: self.write_conf(obj)

        if self._any_have("pools"):
            self.header("#### Pools ####")
            for obj in self.objs: self.write_pools(obj)

        if self._any_have("rules"):
            self.header("#### Rules ####")
            for obj in self.objs: self.write_rules(obj)

        if self._any_have("builds"):
            self.header("#### Build Files ####")
            for obj in self.objs: self.write_builds(obj)

        # clear the objects
        self.objs = []

    def close(self):
        self.flush()
        self.buildfile.close()

    @obj_hasattr("configs")
    def write_conf(self, obj):
        for name, value in obj.configs(self).iteritems():
            self.variable(name, value)
        self.newline()

    @obj_hasattr("pools")
    def write_pools(self, obj):
        for poolname, pool_count in obj.pools(self):
            self.pool(poolname, str(pool_count))
        self.newline()

    @obj_hasattr("rules")
    def write_rules(self, obj):
        for name, opts in obj.rules(self):
            command = opts["command"]
            del opts["command"]
            self.rule(name, command, **opts)
            self.newline()

    @obj_hasattr("builds")
    def write_builds(self, obj):
        for outputs, rule, opts in obj.builds(self):
            self.build(outputs, rule, **opts)
        self.newline()
