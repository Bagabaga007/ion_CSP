from setuptools import setup, find_packages

setup(
    name='ion_CSP',
    version='0.1',
    author='yangze',
    author_email='yangze1995007@163.com',
    description='Crystal Generation Technology Based on Molecular/Ionic Configuration',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    install_requires=[
        'requests',
        'numpy',
    ],
)
