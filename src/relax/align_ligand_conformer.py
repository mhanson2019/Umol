#Map ligand atoms for feature generation
from rdkit import Chem
from rdkit.Chem import AllChem
import numpy as np
from Bio.PDB.PDBParser import PDBParser
from Bio.SVDSuperimposer import SVDSuperimposer
from rdkit.Geometry import Point3D
import pandas as pd
import argparse

import os
import sys
import pdb

parser = argparse.ArgumentParser(description = """Align a conformer generated from RDKIT to the predicted atom positions to ensure realistic bonds etc.""")

parser.add_argument('--pred_pdb', nargs=1, type= str, default=sys.stdin, help = 'Path to input pdb file with predicted protein-ligand positions.')
parser.add_argument('--ligand_smiles', nargs=1, type= str, default=sys.stdin, help = 'ligand smiles string.')
parser.add_argument('--ligand_sdf', nargs=1, type= str, default=sys.stdin, help = 'ligand sdf file.')
parser.add_argument('--outdir', nargs=1, type= str, default=sys.stdin, help = 'Path to output directory. Include /in end')

##############FUNCTIONS##############

def sdf_to_smiles(input_sdf):
    """Read sdf and convert to SMILES
    """
    with Chem.SDMolSupplier(input_sdf) as suppl:
        for mol in suppl:
            return AllChem.MolToSmiles(mol)

def read_pdb(pred_pdb):
    """Read PDB and return atom types and positions
    """

    parser = PDBParser()
    struc = parser.get_structure('',open(pred_pdb,'r'))

    #Save
    chain_coords=[]
    chain_atoms=[]
    chain_bfactors=[]
    chain_atom_numbers=[]

    #Go through al residues
    for model in struc:
        for residue in model['B']:
            res_name = residue.get_resname()
            for atom in residue:

                atom_id = atom.get_id()
                #Save
                chain_coords.append(atom.get_coord())
                chain_atoms.append(atom_id)
                chain_bfactors.append(atom.bfactor)
                chain_atom_numbers.append(atom.serial_number)

    pred_ligand = {'chain_coords': np.array(chain_coords),
                   'chain_atoms':chain_atoms,
                   'chain_bfactors':chain_bfactors,
                   'chain_atom_numbers':chain_atom_numbers
                   }

    return pred_ligand

def generate_best_conformer(pred_coords, ligand_smiles):
    """Generate conformers and compare the coords with the predicted atom positions

    Generating with constraints doesn't seem to work.
    cids = Chem.rdDistGeom.EmbedMultipleConfs(m,max_confs,ps)
    if len([x for x in m.GetConformers()])<1:
        print('Could not generate conformer with constraints')
    """




    #Generate conformers
    m = Chem.AddHs(Chem.MolFromSmiles(ligand_smiles))
    #Embed in 3D to get distance matrix
    AllChem.EmbedMolecule(m, maxAttempts=500)
    bounds=AllChem.Get3DDistanceMatrix(m)
    #Get pred distance matrix
    pred_dmat = np.sqrt(1e-10 + np.sum((pred_coords[:,None]-pred_coords[None,:])**2,axis=-1))
    #Go through the atom types and add the constraints if not H
    #The order here will be the same as for the pred ligand as the smiles are identical
    ai, mi = 0,0
    bounds_mapping = {}
    for atom in m.GetAtoms():
        if atom.GetSymbol()!='H':
            bounds_mapping[ai]=mi
            ai+=1
        mi+=1

    #Assign available pred bound atoms
    bounds_keys = [*bounds_mapping.keys()]
    for i in range(len(bounds_keys)):
        key_i = bounds_keys[i]
        for j in range(i+1, len(bounds_keys)):
            key_j = bounds_keys[j]
            try:
                bounds[bounds_mapping[key_i], bounds_mapping[key_j]]=pred_dmat[i,j]
                bounds[bounds_mapping[key_j], bounds_mapping[key_i]]=pred_dmat[j,i]
            except:
                continue
    #Now generate conformers using the bounds
    ps = Chem.rdDistGeom.ETKDGv3()
    ps.randomSeed = 0xf00d
    ps.SetBoundsMat(bounds)
    max_confs=100
    cids = Chem.rdDistGeom.EmbedMultipleConfs(m,max_confs)
    #Get all conformer dmats
    nonH_inds = [*bounds_mapping.values()]
    conf_errs = []
    for conf in m.GetConformers():
        pos = conf.GetPositions()
        nonH_pos = pos[nonH_inds]
        conf_dmat = np.sqrt(1e-10 + np.sum((nonH_pos[:,None]-nonH_pos[None,:])**2,axis=-1))
        err = np.mean(np.sqrt(1e-10 + (conf_dmat-pred_dmat)**2))
        conf_errs.append(err)


    #Get the best
    best_conf_id = np.argmin(conf_errs)
    best_conf_err = conf_errs[best_conf_id]
    best_conf = [x for x in m.GetConformers()][best_conf_id]
    best_conf_pos = best_conf.GetPositions()

    return best_conf, best_conf_pos, best_conf_err, [atom.GetSymbol() for atom in m.GetAtoms()], nonH_inds, m, best_conf_id


def align_coords_transform(pred_pos, conf_pos, nonH_inds):
    """Align the predicted and conformer positions
    """

    sup = SVDSuperimposer()

    #Set the coordinates to be superimposed.
    #coords will be put on top of reference_coords.
    sup.set(pred_pos, conf_pos[nonH_inds]) #(reference_coords, coords)
    sup.run()
    rot, tran = sup.get_rotran()

    #Rotate coords from new chain to its new relative position/orientation
    tr_coords = np.dot(conf_pos, rot) + tran

    return tr_coords

def format_line(atm_no, atm_name, res_name, chain, res_no, coord, occ, B , atm_id):
    '''Format the line into PDB
    '''

    #Get blanks
    atm_no = ' '*(5-len(atm_no))+atm_no
    atm_name = atm_name+' '*(4-len(atm_name))
    res_name = ' '*(3-len(res_name))+res_name
    res_no = ' '*(4-len(res_no))+res_no
    x,y,z = coord
    x,y,z = str(np.round(x,3)), str(np.round(y,3)), str(np.round(z,3))
    x =' '*(8-len(x))+x
    y =' '*(8-len(y))+y
    z =' '*(8-len(z))+z
    occ = ' '*(6-len(occ))+occ
    B = ' '*(6-len(B))+B

    line = 'HETATM'+atm_no+'  '+atm_name+res_name+' '+chain+res_no+' '*4+x+y+z+occ+B+' '*11+atm_id+'  '
    return line

def write_pdb(coords, atoms, plddt, atm_no, outname):
    """Write a new pdb file of the aligned generated conformer
    """

    with open(outname, 'w') as file:
        for i in range(len(coords)):
            if i<len(plddt):
                plddt_i=plddt[i]
            else:
                plddt_i=1
            atm_no+=1
            file.write(format_line(str(atm_no), atoms[i], atoms[i], 'B', '', coords[i],str(1.00),str(plddt_i), atoms[i])+'\n')


def write_sdf(mol, conf, aligned_conf_pos, best_conf_id, outname):
    """Write sdf file for ligand
    """


    for i in range(mol.GetNumAtoms()):
        x,y,z = aligned_conf_pos[i]
        conf.SetAtomPosition(i,Point3D(x,y,z))

    writer = Chem.SDWriter(outname)
    writer.write(mol, confId=int(best_conf_id))


##################MAIN#######################

#Parse args
args = parser.parse_args()
#Data
pred_ligand = read_pdb(args.pred_pdb[0])
try:
    ligand_smiles = args.ligand_smiles[0]
except:
    ligand_smiles = sdf_to_smiles(args.ligand_sdf[0])
outdir = args.outdir[0]

#Get a nice conformer
best_conf, best_conf_pos, best_conf_err, atoms, nonH_inds, mol, best_conf_id  = generate_best_conformer(pred_ligand['chain_coords'], ligand_smiles)
#Save error
conf_err = pd.DataFrame()
#conf_err['id'] = [args.pred_pdb[0].split('/')[-1].split('_')[0]]
conf_err['id'] = [args.pred_pdb[0].split('/')[-1].split('.')[1]] # id is the ligand name
conf_err['conformer_dmat_err'] = best_conf_err
conf_err.to_csv(outdir+'conformer_dmat_err.csv', index=None)
#Align it to the prediction
aligned_conf_pos = align_coords_transform(pred_ligand['chain_coords'], best_conf_pos, nonH_inds)

#Write sdf - better to define bonds
write_sdf(mol, best_conf, aligned_conf_pos, best_conf_id, outdir+conf_err['id'].values[0]+'_pred_ligand.sdf')

#Write pdb with ligand file
#write_pdb(aligned_conf_pos, atoms, pred_ligand['chain_bfactors'], pred_ligand['chain_atom_numbers'][0], outdir+'best_ligand_conf.pdb')
