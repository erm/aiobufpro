from setuptools import find_packages, setup

from aiobufpro.__version__ import __version__


def get_long_description():
    return open("README.md", "r", encoding="utf8").read()


setup(
    name="aiobufpro",
    version=__version__,
    packages=find_packages(),
    install_requires=["starlette"],
    license="MIT License",
    author="Jordan Eremieff",
    author_email="jordan@eremieff.com",
    url="https://github.com/erm/aiobufpro",
    description="An experimental ASGI server for Python 3.7+",
    long_description=get_long_description(),
    long_description_content_type="text/markdown",
    entry_points={"console_scripts": ["aiobufpro = aiobufpro.server:main"]},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Internet :: WWW/HTTP",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
    ],
)
