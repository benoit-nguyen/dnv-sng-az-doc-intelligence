"""Setup configuration for Document Processor package."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="docprocessor",
    version="0.1.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Azure Document Intelligence Batch Document Processor",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/dnv-sng-az-doc-intelligence",
    packages=find_packages(exclude=["tests", "examples", "docs"]),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.11",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "docprocessor=docprocessor.cli:main",
        ],
    },
)
