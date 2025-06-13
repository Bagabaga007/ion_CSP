from setuptools import setup, find_packages
import importlib.util


def get_version():
    spec = importlib.util.spec_from_file_location("ion_CSP", "src/ion_CSP/__init__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.__version__

setup(
    name="ion_CSP",
    version=get_version(),
    author="Ze Yang",
    author_email="yangze1995007@163.com",
    description="Crystal Structure Prediction Technology Based on Molecular/Ionic Configuration",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/bagabaga007/ion_CSP",
    license="MIT",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: Linux",
    ],
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    include_package_data=True,
    package_data={
        "": ["param/*", "model/model.pt", "README.md"]
    },
    python_requires=">=3.11",
    entry_points={
        "console_scripts": [ 
            "ion-csp = ion_CSP.task_manager:main", 
                            ]
    },
)
