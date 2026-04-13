from setuptools import setup, find_packages

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="engram-echo",
    version="0.1.1",
    description="Persistent memory for Claude Code — knowledge graph in Obsidian vault",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="qianheng",
    author_email="hengqian.qh@gmail.com",
    url="https://github.com/qianheng-aws/engram",
    license="MIT",
    packages=find_packages(),
    py_modules=["engram_cli"],
    python_requires=">=3.10",
    install_requires=["networkx"],
    entry_points={"console_scripts": ["engram=engram_cli:main"]},
    include_package_data=True,
    package_data={
        "plugin": [
            ".claude-plugin/*.json",
            "bin/*",
            "commands/*.md",
            "hooks/*.json",
        ],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries",
    ],
    keywords="claude-code memory knowledge-graph obsidian",
)
