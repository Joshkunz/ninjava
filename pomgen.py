#!/usr/bin/env python
from pypom import Mvn, Resolver, resolve

R = Resolver("http://repo2.maven.org/maven2",
             "http://repository.jboss.org",
             "http://maven.restlet.org",
             exclude=["javax.xml.bind", "javax.xml.stream"])
           
#             exclude=["org.jboss.pressgang", "org.jboss.jdocbook"])

#### Dependency Variables ####

DROOLS_VERSION = "5.5.0.Final"
SLF4J_VERSION = "1.7.5"
RESTLET_VERSION = "2.1.2"

### Dependencies 
DEPS = [
    Mvn("org.drools",                   
        ("drools-core", DROOLS_VERSION),
        ("knowledge-api", DROOLS_VERSION),
        #("knowledge-internal-api", DROOLS_VERSION),
        ("drools-compiler", DROOLS_VERSION)),
    Mvn("org.slf4j", 
        ("slf4j-api", SLF4J_VERSION),
        ("slf4j-log4j12", SLF4J_VERSION)),
    Mvn("log4j", ("log4j", "1.2.9")),
    Mvn("args4j", ("args4j", "2.0.21")),
    Mvn("org.codeartisans",
        ("org.json", "20130213")),
    Mvn("org.restlet.jse", 
        ("org.restlet", RESTLET_VERSION),
        ("org.restlet.ext.json", RESTLET_VERSION),
        ("org.restlet.ext.slf4j", RESTLET_VERSION)),
    Mvn("org.jdom", ("jdom2", "2.0.4")),
    Mvn("org.apache.httpcomponents", 
        ("httpclient", "4.2.3"),
        ("httpcore", "4.2.3"))
]

from pprint import pprint
pprint(resolve(R, *DEPS))
