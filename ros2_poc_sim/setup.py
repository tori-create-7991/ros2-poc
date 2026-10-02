import glob
import os

from setuptools import find_packages, setup

package_name = 'ros2_poc_sim'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config', 'profiles'), glob.glob('config/profiles/*.yaml')),
        (os.path.join('share', package_name, 'config', 'placements'), glob.glob('config/placements/*.yaml')),
        (os.path.join('share', package_name, 'config', 'contracts'), glob.glob('config/contracts/*.yaml')),
        (os.path.join('share', package_name, 'models', 'blue_cube'), glob.glob('models/blue_cube/*')),
        (os.path.join('share', package_name, 'launch'), glob.glob('launch/*.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='ros2-poc',
    maintainer_email='noreply@example.com',
    description='Camera profile layer for Gazebo virtual cameras',
    license='Apache-2.0',
    entry_points={
        'console_scripts': [
            'camera_adapter = ros2_poc_sim.camera_adapter:main',
            'contract_check = ros2_poc_sim.contract_check:main',
        ],
    },
)
