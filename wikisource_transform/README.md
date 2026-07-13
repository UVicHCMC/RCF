# WikiSource to RCF XML Transformation

This repository contains a python script that converts texte-entier JSON files that are output by the Wikisource API into RCF-standard XML files. 

To use it, set up your python environment and install lxml using 'pip install lxml'. Then, run it using the command: 

python3 wikisource_transform.py src/input.json xml/output.xml

To get JSON files follow these steps:
1. Find the periodical you want on WikiSource (eg https://fr.wikisource.org/wiki/Mercure_de_France).
2. Find the year/volume you wish to download.
3. Ideally, the volume will have a texte sur une seule page/texte entier link at the top. If so, click that link. If not, you will have to download and transform each section one at a time. 
4. Copy the last section of the link for the volume/section you wish to download. 
For example, if the full URL is https://fr.wikisource.org/wiki/Mercure_galant,_juin,_juillet_et_ao%C3%BBt_1710/Texte_entier you will want to copy "Mercure_galant,_juin,_juillet_et_ao%C3%BBt_1710/Texte_entier" (sans quotes).
5. Use the API to download the JSON file. API JSON download links follow this pattern:
https://fr.wikisource.org/w/api.php?action=parse&page={Text URI}&prop=text&format=json
So, for the example above, you would use the following link to get the JSON file: 
https://fr.wikisource.org/w/api.php?action=parse&page=Mercure_galant,_juin,_juillet_et_ao%C3%BBt_1710/Texte_entier&prop=text&format=json

For more information on the WikiSource API, see https://en.wikisource.org/w/api.php

You can then use the python script to convert the JSON file to XML. 
