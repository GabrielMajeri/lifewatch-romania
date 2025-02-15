import openeo

print("Connecting to Copernicus Dataspace and retrieving dataset metadata")
connection = openeo.connect("openeo.dataspace.copernicus.eu")

# List collections available on the openEO back-end
print("Collection IDs:", connection.list_collection_ids())

print()

# Get detailed metadata of a certain collection
print(
    "Sentinel 2 L2A collection metadata:",
    connection.describe_collection("SENTINEL2_L2A"),
)

print()

print("Authenticating to the Copernicus Dataspace using OIDC")
connection.authenticate_oidc()

print("Creating a data cube")
datacube = connection.load_collection(
    "SENTINEL2_L2A",
    spatial_extent={"west": 5.14, "south": 51.17, "east": 5.17, "north": 51.19},
    temporal_extent=["2021-02-01", "2021-04-30"],
    bands=["B02", "B04", "B08"],
    max_cloud_cover=85,
)

# From this data cube, we can now select the individual bands with the DataCube.band() method and rescale the digital number values to physical reflectances:
blue = datacube.band("B02") * 0.0001
red = datacube.band("B04") * 0.0001
nir = datacube.band("B08") * 0.0001

# We now want to compute the enhanced vegetation index and can do that directly with these band variables:
evi_cube = 2.5 * (nir - red) / (nir + 6.0 * red - 7.5 * blue + 1.0)

"""
# Now we can use the compact “band math” feature again to build a binary mask with a simple comparison operation:
# Select the "SCL" band from the data cube
scl_band = datacube.band("SCL")

# Build mask to mask out everything but class 4 (vegetation)
mask = scl_band != 4

# Before we can apply this mask to the EVI cube we have to resample it, as the “SCL” layer has a “ground sample distance” of 20 meter, while it is 10 meter for the “B02”, “B04” and “B08” bands. We can easily do the resampling by referring directly to the EVI cube.
mask_resampled = mask.resample_cube_spatial(evi_cube)

# Apply the mask to the `evi_cube`
evi_cube = evi_cube.mask(mask_resampled)
"""

# Because GeoTIFF does not support a temporal dimension, we first eliminate it by taking the temporal maximum value for each pixel:
evi_composite = evi_cube.max_time()

print("Downloading composite image to disk")

# Now we can download this to a local file:
evi_composite.download("evi-composite.tiff")
