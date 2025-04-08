from setuptools import setup, find_packages

with open('README.md', 'r', encoding='utf-8') as file:
    long_description = file.read()

setup(
    name='ion_CSP',
    version='1.0.0',
    author='yangze',
    author_email='yangze1995007@163.com',
    description='Crystal Generation Technology Based on Molecular/Ionic Configuration',
    long_description=long_description,
    url='https://github.com/bagabaga007/ion_CSP',
    classifiers = [
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: Linux'
        ],
    packages=find_packages('src'),
    package_dir={'':'src'},
    python_requires='>=3.11',
)
