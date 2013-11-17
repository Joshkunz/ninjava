import textwrap

import os
from os import path
import re
import functools
from pprint import pprint

import httplib
import requests
from lxml import etree

from collections import deque

class Mvn(object):

    def __init__(self, org, *tuples):
        self.org = org
        self.artifacts = tuples

class PomResolver(object):
    CACHE = {}

    def __init__(self, *urls):
        self.repos = urls

    @classmethod
    def url_join(cls, base, path):
        if base[-1] == "/" and path[0] == "/": 
            url = base + path[1:]
        elif base[-1] != "/" and path[0] != "/":
            url = "/".join((base, path))
        else:
            url = base + path
        return url

    def extend(self, urls):
        new_repos = self.repos[:]
        for url in urls:
            new_repos.append(url)
        return PomResolver(*new_repos)

    def append(self, url):
        return self.extend([url])

    def _check_cache(self, path):
        for repo in self.repos:
            url = self.url_join(repo, path)
            if url in self.CACHE:
                return self.CACHE[url]

    def get_path(self, path):
        cached_copy = self._check_cache(path)
        if cached_copy is not None:
            return cached_copy

        for repo in self.repos:
            url = self.url_join(repo, path)
            print "FETCHING {0}".format(url),
            response = requests.get(url, stream=True)
            if response.status_code != 200: 
                print httplib.responses[response.status_code].upper()
                continue
            print "OK"
            self.CACHE[url] = etree.parse(response.raw)
            return self.CACHE[url]
        raise ValueError("Could not fetch: {0}".format(path))

    def get_pom(self, artifact):
        """Attempt to get a path from all repos and return the path as xml"""
        xml = self.get_path(artifact.pompath)
        return Pom(artifact, xml, resolver=self)

class Properties(object):
    "A dictionary interface into the properties functionalities of a POM"

    PROP_MATCHER = re.compile(r"\$\{([a-zA-Z0-9.-]*)\}")

    def __init__(self, pom, other_properties):
        self.ns = pom.xml.getroot().nsmap.get(None, "")
        self.pom = pom
        self.other = other_properties
        self.other.update(self._parse_properties(pom, self.ns))

    def _check_env(self, name):
        if name.split(".", 1)[0].lower() == "env":
            return os.environ[name.split(".", 1)[1]]

    def _check_doc(self, name):
        search_path = name.split(".")
        namespaces = {}
        if self.ns:
            xpath = "/" + ("/".join(map(lambda x: "maven:" + x, search_path)))
            namespaces = {"maven": self.ns}
        else:
            xpath = "/" + ("/".join(search_path))
        search = self.pom.xml.xpath(xpath, namespaces = namespaces)
        if search: return search[0].text

    def _sub_replacer(self, match):
        varname = match.group(1)
        return self.get(varname, "${%s}" % varname)

    def resolve(self, text, child=False):
        match = self.PROP_MATCHER.match(text)
        # If we can resolve this variable, resolve it and then try to re-evaluate
        # it again to account of recursive variable definition
        if match is not None and self.get(match.group(1)) is not None:
            return self.resolve(self.PROP_MATCHER.sub(self._sub_replacer, text))
        return text

    def update(self, d):
        self.other.update(d)

    def get(self, name, default=None, from_child=False):
        try:
            return self.__getitem__(name, from_child=from_child)
        except KeyError, e:
            return default

    def _check_parent(self, name):
        if self.pom.parent is None: return None
        return self.pom.parent_pom.properties.__getitem__(name, from_child=True)

    def __getitem__(self, name, from_child=False):
        if not from_child:
            env = self._check_env(name)
            if env is not None: return env

            doc = self._check_doc(name)
            if doc is not None: return doc

        if name in self.other:
            return self.other[name]

        # If it cannot be resolved locally, check the parent POM
        from_parent = self._check_parent(name)
        if from_parent is not None:
            return from_parent

        raise KeyError(name)

    def _parse_properties(self, pom, ns):
        """Parse the properties section of a POM file 
           (used for finding required versions)"""

        if ns:
            props = pom.xml.xpath("//maven:properties", namespaces={"maven": ns})
        else:
            props = pom.xml.xpath("//properties")

        properties = {}

        for property_list in props:
            for prop in property_list.iterchildren():
                # Skip comments
                if not isinstance(prop.tag, str): continue
                name = etree.QName(prop.tag)
                properties[name.localname] = prop.text
        return properties

class Pom(object):

    def __init__(self, artifact, xml, resolver=None):
        self.artifact = artifact
        self.xml = xml
        self.ns = self.xml.getroot().nsmap.get(None, "")
        self.resolver = resolver

        self.properties = self._properties()

    def _properties(self):
        return Properties(self, {
            "pom.groupId": self.artifact.org,
            # Last-ditch project version
            "project.version": self.artifact.version
        })

    @property
    def parent_pom(self):
        if self.parent is not None:
            return self.resolver.get_pom(self.parent)

    @property
    def parent(self):
        parent = self.xml.find("{%s}parent" % self.ns)
        if parent is not None:
            return Artifact.from_element(parent, self.properties, 
                                         resolver=self.resolver)

    @property
    def repositories(self):
        xpath = "/project/repositories/repository"
        namespaces = {}
        if self.ns:
            xpath = "/maven:project/maven:repositories/maven:repository/url"
            namespaces = {"maven": self.ns}
        for url_elem in self.xml.xpath(xpath, namespaces = namespaces):
            yield url_elem.text

    @property
    def deps(self):
        xpath = "/project/dependencies/dependency"
        namespaces = {}
        if self.ns: 
            xpath = "/maven:project/maven:dependencies/maven:dependency"
            namespaces = {"maven": self.ns}
        for dep_elem in self.xml.xpath(xpath, namespaces=namespaces):
            yield Dependency.from_element(dep_elem, self.properties, 
                                          resolver=self.resolver)
        # Get upper-level dependencies as well..
        if self.parent is not None:
            for dep in self.parent_pom.parent_deps:
                yield dep

    @property
    def parent_deps(self):
        # Some POM files don't use the maven schema 
        xpath = "//dependencyManagement/dependencies/dependency"
        namespaces = {}
        if self.ns: 
            xpath = "//maven:dependencyManagement/maven:dependencies/maven:dependency"
            namespaces = {"maven": self.ns}
        for dep_elem in self.xml.xpath(xpath, namespaces = namespaces):
            yield Dependency.from_element(dep_elem, self.properties, 
                                          resolver=self.resolver)
        # Get the parental dependencies as well
        if self.parent is not None:
            for dep in self.parent_pom.parent_deps:
                yield dep

class Artifact(object):
    META_EXT = "maven-metadata.xml"

    def __init__(self, org, artifact, version=None, 
                 resolver=None, version_map={}, can_skip_version=False):
        self.resolver = resolver 
        self.org = org
        self.artifact = artifact
        self.version = version
        if self.version is None and resolver is not None:
            try:
                self.version = self._fetch_version(resolver, version_map)
            except ValueError, e:
                if can_skip_version: pass
                else: raise e
        elif self.version is None and not can_skip_version:
            raise ValueError("No resolver found for artifact: {0}."\
                             .format(repr(self)))

    @classmethod
    def from_element(cls, element, properties, exclude=[], 
                     resolver=None, can_skip_version=False):
        ns = element.nsmap[None] if None in element.nsmap else ""
        org = properties.resolve(element.find("{%s}groupId" % ns).text)
        # skip excluded organizations
        if org.lower() in exclude: return None
        artifact = properties.resolve(element.find("{%s}artifactId" % ns).text)
        version = element.find("{%s}version" % ns)
        if version is not None:
            version = properties.resolve(version.text)
        return cls(org, artifact, version, resolver=resolver, 
                   can_skip_version=can_skip_version)

    @property
    def tuple(self):
        return (self.org, self.artifact, self.version)

    @property
    def slashed_org(self):
        return self.org.replace(".", "/")

    def urlpath(self, ext="jar"):
        filename = "{artifact}-{version}.{ext}".format(ext=ext, **self.__dict__)
        return "/".join((self.slashed_org, self.artifact, self.version, filename))

    @property
    def jarpath(self):
        return self.urlpath(ext="jar")

    @property
    def pompath(self):
        return self.urlpath(ext="pom")

    def __eq__(self, other):
        return self.tuple == other.tuple

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.tuple)

    def __repr__(self):
        return repr(self.tuple)

    def _fetch_version(self, resolver, version_map={}):
        "Find the latest version of this artifact"
        try:
            meta = resolver.get_path("/".join((self.slashed_org, 
                                               self.artifact, 
                                               self.META_EXT)))
        except ValueError, e:
            if (self.org, self.artifact) in version_map:
                return version_map[(self.org, self.artifact)]
            else: raise e
        # Attempt the get the stated release version
        release = meta.xpath("/metadata/versioning/release")
        # Otherwise the latest version
        latest = meta.xpath("/metadata/versioning/latest")
        # Otherwise the first version listed
        first = meta.xpath("/metadata/versioning/versions/version")
        if len(release) > 0: return release[0].text
        elif len(latest) > 0: return latest[0].text
        elif len(first) > 0: return first[0].text
        elif (self.org, self.artifact) in version_map:
            return version_map[(self.org, self.artifact)]
        else:
            raise ValueError("Could not parse version for {0}/{1}"\
                             .format(self.org, self.artifact))

class Dependency(object):
    "Artifact encapsulted with dependency information"

    def __init__(self, artifact, scope=None, optional=False, type=None, excludes=[]):
        self.artifact = artifact
        self.scope = "compile" if scope is None else scope.lower()
        self.type = "jar" if type is None else type.lower()
        self.optional = optional
        self.excludes = excludes

    @classmethod
    def from_element(cls, element, properties, resolver=None, can_skip_version=True):
        ns = element.nsmap[None]
        artifact = Artifact.from_element(element, properties, 
                                         resolver=resolver,
                                         can_skip_version=can_skip_version)
            
        # Return none if this is an excluded artifact
        if artifact is None: return None

        scope = element.find("{%s}scope" % ns)
        if scope is not None: scope = scope.text

        optional = element.find("{%s}optional" % ns)
        if optional is not None:
            optional = True if optional.text.lower() == "true" else False

        type = element.find("{%s}type" % ns)
        if type is not None:
            type = type.text

        exclusions = []
        xpath = "/exclusions/exclusion"
        namespaces = {}
        if ns:
            xpath = "/maven:exclusions/maven:exclusion"
            namespaces = {"maven": ns}
        for exclusion in element.xpath(xpath, namespaces = namespaces):
            exclusions.append(Artifact.from_element(exclusion, properties, 
                              resolver=resolver, 
                              can_skip_version=can_skip_version))

        return Dependency(artifact, scope, optional, type, exclusions)

    def __repr__(self):
        return "<Dependency {0} scope:{1} optional:{2} type:{3}>"\
                .format(repr(self.artifact), self.scope, self.optional, self.type)

    def __eq__(self, other_dep):
        return self.__hash__() == other_dep.__hash__()

    def __ne__(self, other_dep): return not self.__eq__(self, other_dep)

    def __hash__(self):
        return hash((self.artifact, self.scope, self.optional, 
                     self.type, tuple(self.excludes)))


class Resolver(object):

    def __init__(self, *repos, **kwargs):
        """ Keyword arguments:
            transitive: Whether or not to fetch transitive dependinces
            exclude: A list of organizations to skip due to broken POM files
            version_map: A last resort mechanism for resolving artifact versions
            scopes: A list of scopes to include in the dependency resolution
        """
        self.repos = repos

        # Cant be included in function signature due to *repos
        self.transitive = kwargs.get("transitive", True) 
        self.exclude = kwargs.get("exclude", []) 
        self.version_map = kwargs.get("version_map", {}) 
        self.scopes = map(lambda x: x.lower(), 
                          kwargs.get("scopes", ["compile", "runtime"]))
        self.types = map(lambda x: x.lower(),
                         kwargs.get("types", ["jar"]))
        self.dep_cache = {}

    @property
    def default_resolver(self):
        return PomResolver(*self.repos)

    def resolve(self, *mvn_groups):
        artifact_list = []
        for group in mvn_groups:
            for (artifact_, version) in group.artifacts:
                dep = Dependency(Artifact(group.org, artifact_, 
                                          version=version, 
                                          resolver=self.default_resolver))
                artifact_list.append(dep)
                artifact_list.extend(self.full_deps(dep))
        return [a.jarpath for a in artifact_list]

    def add_repo(self, *urls):
        "Add a maven repository to be used in lookups"
        for repo in urls: 
            if repo not in self.repos: self.repos.append(repo)

    def full_deps(self, dependency):
        "Fetch all the dependencies for this artifact"

        resolver = self.default_resolver

        # the current list of dependent packages and thier versions 
        resolve_queue = deque([dependency])
        deps = set()

        while len(resolve_queue) > 0:
            current  = resolve_queue.popleft()
            if current in deps: continue
            pom = resolver.get_pom(current.artifact)

            for dep in pom.deps:
                if dep.optional or dep.scope not in self.scopes: continue
                if dep.type not in self.types: continue
                if dep.artifact in current.excludes: continue
                if dep in resolve_queue: continue
                print "Got Dep:", dep
                resolve_queue.append(dep)
            deps.add(current)
        return deps
