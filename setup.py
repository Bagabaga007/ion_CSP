from setuptools import setup, find_packages

setup(
    name='ion_CSP',
    version='2.0.2',
    author='yangze',
    author_email='yangze1995007@163.com',
    description='Crystal Generation Technology Based on Molecular/Ionic Configuration',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
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
