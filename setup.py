#!/usr/bin/env python
"Setuptools params"

from setuptools import setup, find_packages
from os.path import join

# Get version number from source tree
import sys

sys.path.append('.')

scripts = [join('bin', filename) for filename in ['sn']]

modname = distname = 'orbit-graph'

setup(
    name=distname,
    version="1.0.0",
    description=
    'OrbitGraph: SDN-based routing for LEO satellite constellations, built on the StarryNet emulator.',
    author='Gustavo Pantuza',
    packages=find_packages(),
    long_description="""
        OrbitGraph is a fork of the StarryNet emulator (Lai et al., NSDI 2023)
        extended with a centralized SDN controller that computes and installs
        proactive, graph-based routing for Low Earth Orbit constellations.
        """,
    classifiers=[
        "License :: OSI Approved :: BSD License",
        "Programming Language :: Python",
        "Development Status :: 1 - Production/Stable",
        "Intended Audience :: Developers",
        "Topic :: System :: Emulators",
    ],
    keywords='LEO satellite constellations SDN routing emulator OrbitGraph',
    license='BSD',
    install_requires=['setuptools'],
    scripts=scripts,
)
