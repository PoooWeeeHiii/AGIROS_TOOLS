from __future__ import print_function

from .agirosdebian import AgirosDebianGenerator

from .generate_cmd import main, prepare_arguments

description = dict(
    title='agirosdebian',
    description="Generates debian packaging files for a catkin package (AGIROS extended)",
    main=main,
    prepare_arguments=prepare_arguments,
)

__all__ = ['AgirosDebianGenerator', 'main', 'prepare_arguments', 'description']
