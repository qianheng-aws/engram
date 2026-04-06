from setuptools import setup, find_packages

setup(
    name="ooda-memory",
    version="0.1.0",
    description="Persistent memory for Claude Code — knowledge graph in Obsidian vault",
    author="qianheng",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=["networkx"],
    entry_points={"console_scripts": ["ooda-dream=dream_cli:main"]},
)
