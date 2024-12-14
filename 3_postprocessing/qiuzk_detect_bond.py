import time
import os,sys,glob,time 
import numpy as np
import argparse
import torch
from ase.io import read 
from ase.geometry.analysis import Analysis



def find_bond(atoms1):

    ana_str1 = Analysis(atoms1)
    bond1 = []
    resd = []
    atoms_symbols = atoms1.get_chemical_symbols() 
    element, ele = Get_Element_Num(atoms_symbols)
    for ii in range(len(element)):
        for jj in range(ii,len(element)):
            bondone = ana_str1.get_bonds(element[ii],element[jj])
            if len(bondone[0]) != 0:
                bondvalue = ana_str1.get_values(bondone)
                for bond in bondone[0]:
                    bond1.append([bond[0],bond[1]])
                for abvalue in bondvalue[0]:
                    resd.append(abvalue)
                    
    resd = torch.tensor(resd)
    resd = resd.reshape(-1,1)
    return bond1,resd

def Get_Element_Num(elements):
    
    '''Using the Atoms.symples to Know Element&Num'''
    element = []
    ele = {}
    element.append(elements[0])
    for x in elements: 
        if x not in element :
            element.append(x)
    for x in element: 
        ele[x] = elements.count(x)
    return element, ele 


# pospath = '/home/qiuzk/work/method_test_lincs/test2/newdata/data/step001.pop001/CONTCAR_1'
pospath = '/workplace/yz/Test/yz_opt/ion_CSP/3_postprocessing/CONTCAR'
atoms = read(pospath,format='vasp') 
bond,resd = find_bond(atoms)
print(bond)
print(resd)