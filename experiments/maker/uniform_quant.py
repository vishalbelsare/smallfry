import torch
import numpy as np
import math
from scipy.interpolate import interp1d


'''
HIGH-LEVEL QUANTIZATION CALLS
'''
def stochround(X,b):
    return uniform_quantizer(X, b, _fullrange, _stochround)
def naiveuni(X,b):
    return uniform_quantizer(X, b, _fullrange, _round)
def optranuni(X,b):
    return uniform_quantizer(X, b, _adarange, _round)
def stochoptranuni(X,b):
    return uniform_quantizer(X, b, _adarange, _stochround)
def clipnoquant(X,b):
    return uniform_quantizer(X, b, _adarange, lambda Y: Y)
def midriser(X,b):
    raise ValueError("Midriser is no longer supported")
def stochoptranuni2(X,b):
    return uniform_quantizer(X, b, _stoch_adarange, _stochround)
'''
CORE QUANTIZER
'''
def uniform_quantizer(X, b, q_range, quantize):
    b = int(b)
    X = torch.Tensor(X)
    L = q_range(X, b)
    forward_map = lambda Y: _affine_transform(Y,L,b)
    backward_map = lambda Y: _affine_transform(Y,L,b,invert=True)
    return backward_map(quantize(forward_map(_clip(X,L)))).numpy()

'''
Range solvers
'''
def _adarange(X,b):    
    return golden_section_search(lambda L : np.linalg.norm(X.numpy() - uniform_quantizer(
        X, b, lambda Y,z : L, _round)),1e-10,20)

def _stoch_adarange(X,b):
    return golden_section_search(lambda L : np.linalg.norm(X.numpy() - uniform_quantizer(
        X, b, lambda Y,z : L, _stochround)),1e-10,20)

def _fullrange(X,b):
    return torch.max(torch.abs(X))

'''
Rounding schemes
'''
def _stochround(X):
    return torch.ceil(X - torch.rand(X.shape))

def _round(X):
    return torch.round(X)

'''
HELPERS
'''
def _affine_transform(X,L,b,invert=False):
    n = 2**b-1
    interval = 2*L
    shift = 0.5
    return (X/n - shift)*interval if invert else  n*(X/interval + shift)

def _clip(X,L):
    eps = 1e-10
    L -= eps
    return torch.clamp(X, min=-1*L, max=L)

def goldensearch(f,eps=1e-10,tol=0.1,L_max=10):
    '''
    Implements the golden section line search
    Adaptively finds optimal range based on data
    '''
    phi = (1+np.sqrt(5))/2 # golden ratio
    #initialize line search iteration
    a = eps
    b = L_max
    val_a = f(a)
    val_b = f(b)
    c = b - (b-a)/phi
    d = a + (b-a)/phi
    val_c = f(c)
    val_d = f(d)
    #perform iterations
    while (b-a > tol):
        if val_c < val_d:
            b = d
            val_b = val_d
            d = c
            val_d = val_c
            c = b - (b-a)/phi
        else:
            a = c
            val_a = val_c
            c = d
            val_c = val_d
            d = a + (b-a)/phi
        val_c = f(c)
        val_d = f(d)
    #on termination, return optimal range
    return c if val_c < val_d else d

"""
def stochround(X,b,seed):
    '''
    Implements random uniform rounding over entire range [-L,L]
    L = max(abs(X))
    '''
    b = int(b)
    torch.manual_seed(seed)
    dtype = torch.cuda.FloatTensor
    X = torch.Tensor(X)
    L = torch.max( torch.abs( X )) # compute range
    n = 2**b - 1
    X = X / (2*L) # apply affine transform to get on unit interval
    X = X+0.5
    X = n*X # apply linear transform to put each quanta at integer
    X = X - torch.rand(X.shape) # each entry will round down if noise > fraction part
    X = torch.ceil(X)
    X = X/n #undo linear transform
    X = X-0.5 #undo shift
    X = X*2*L #put back in original range
    return X

def midriser(X,b):
    '''
    Implements deterministc midriser uniform quantization over entire range [-L,L]
    L = max(abs(X))
    '''
    b = int(b)
    delta = 1/2**b
    dtype = torch.cuda.FloatTensor
    X = torch.Tensor(X)
    eps = 1e-5
    L = torch.max( torch.abs( X )) + eps # compute range
    X = X / (2*L) # apply affine transform to get on unit interval
    X = X+0.5
    X = delta*( torch.floor(X/delta) + 0.5)
    X = X - 0.5
    X = X*2*L
    return X

def naiveuni(X,br):
    L = np.max(np.abs(X))
    return uniquant(X,br,L).numpy()
    
def uniquant(X,br,L):
    '''Copies X, quantizes X, returns X. Uses range L and bitrate br'''
    X_q = torch.Tensor(X)
    X_q = torch.clamp(X_q, min=-1*L, max=L)
    n = 2**br - 1
    X_q = (X_q+L)/(2*L)
    X_q = n*X_q # apply linear transform to put each quanta at integer
    X_q = torch.round(X_q)
    X_q = X_q/n #undo linear transform
    X_q = X_q*2*L - L #undo shift
    return X_q

def optranuni(X,br,eps=1e-40,tol=0.1,L_max=10):
    '''
    Implements the golden section line search
    Adaptively finds optimal range based on data
    Deterministic uniform rounding over optimal range
    '''
    br = int(br)
    if br == 32:
        return X
    quant = lambda X,L: uniquant(X,br,L) #bitrate does not change, no reason to pass it in each time
    f = lambda X,X_q: _compute_frobenius(X,X_q)
    L_star = _goldensearch(X,f,quant,eps=eps,tol=tol,L_max=L_max)
    X_q = quant(X,L_star)
    return X_q.numpy()

def stochoptranuni(X,br,seed=1234,eps=1e-40,tol=0.1,L_max=10):
    '''
    Implements the golden section line search
    Adaptively finds optimal range based on data
    Deterministic uniform rounding over optimal range
    '''
    br = int(br)
    if br == 32:
        return X
    quant = lambda X,L: uniquant(X,br,L) #bitrate does not change, no reason to pass it in each time
    f = lambda X,X_q: _compute_frobenius(X,X_q)
    L_star = _goldensearch(X,f,quant,eps=eps,tol=tol,L_max=L_max)
    X_c = np.clip(X,-1*(L_star+eps),L_star+eps)
    X_q = stochround(X_c,br,seed)
    #X_q = clamp_and_quantize(torch.from_numpy(X), br, range_limit=L_star, stochastic_round=True)
    return X_q.numpy()

def clipnoquant(X,br):
    '''
    Clips where it would clip with oprtanuni, but otherwise full-precision 
    '''
    br = int(br)
    quant = lambda X,L: uniquant(X,br,L) #bitrate does not change, no reason to pass it in each time
    f = lambda X,X_q: _compute_frobenius(X,X_q)
    L_star = _goldensearch(X,f,quant)
    X_clip = torch.clamp(torch.from_numpy(X), min=-1*L_star, max=L_star)
    return X_clip.numpy()



def _compute_frobenius(baseX,X_q):
        '''Value we are minimizing -- Frobenius distance'''
        return np.linalg.norm(baseX-X_q)

'''
This method clamps X between -range_limit and +range_limit, and then quantizes
X into one of 2**br possible values, using mid_riser quantization values or not,
and using stochastic or deterministic rounding.
If bit_rate == 32, no quantization is done. 
If range_limit == np.inf, no clamping is done.
QUESTION: DO WE WANT TO CHANGE X IN-PLACE?
'''
def clamp_and_quantize(X, bit_rate=32, range_limit=np.inf, stochastic_round=False):
    assert range_limit >= 0, 'range_limit must be non-negative.'
    do_clamp = range_limit != np.inf
    do_quantize = bit_rate < 32
    if range_limit == np.inf:
        range_limit = get_max_abs(X)
    if do_quantize and use_midriser:
        # The only difference between 'midriser' and 'not midriser' is that 'midriser'
        # picks the 2**bit_rate quantization values between 
        # [-range_limit + r, range_limit - r] for r = range_limit/2**bit_rate,
        # while 'not midriser' chooses the 2**bit_rate quantization values between
        # [-range_limit,+range_limit].
        range_limit -= range_limit / 2**bit_rate
    X_q = X.clone() # creates a copy of X
    if use_midriser or do_clamp:
        # When using mid-riser, always need to clamp.
        # If not using mid-riser, only need to clamp if user specified a range_limit.
        X_q = torch.clamp(X_q, min=-range_limit, max=range_limit)
    if do_quantize:
        X_q = quantize(X_q, bit_rate, range_limit, stochastic_round=stochastic_round)
        #X_q = quantize_with_scipy(X_q,range_limit, bit_rate, stochastic_round=stochastic_round)
    return X_q

'''
X is a Tensor where all entries are between -range_limit and +range_limit.
Letting L=range_limit, and r = 2*L/(2**bit_rate-1), this method quantizes X 
into one of the values in {-L,-L+r,-L+2r,...,+L} (a set of size 2**bit_rate)
QUESTION: DO WE WANT TO CHANGE X IN-PLACE?
'''
def quantize(X, bit_rate, range_limit, stochastic_round=False):
    assert range_limit != np.inf and range_limit >= 0, 'range_limit must be finite and non-negative.'
    assert get_max_abs(X) <= range_limit, 'X must be between -range_limit and +range_limit'
    assert bit_rate < 32, 'Only bit_rates < 32 supported.'
    # affine transform to put X in [0,2**bit_rate - 1]
    X_q = (2**bit_rate - 1) * (X + range_limit) / (2 * range_limit) # not in-place
    if stochastic_round:
        X_q = X_q - torch.rand(X_q.shape)
        # each entry will round down if noise > fraction part
        X_q = torch.ceil(X_q)
    else:
        X_q = torch.round(X_q)
    # undo affine transformation
    X_q = (X_q * 2 * range_limit) / (2**bit_rate - 1) - range_limit 
    return X_q


'''
def quantize_with_scipy(X, bit_rate, range_limit, stochastic_round=False):
    assert range_limit != np.inf, 'range_limit must be finite.'
    assert get_max_abs(X) <= range_limit, 'X must be between -range_limit and +range_limit'
    assert bit_rate < 32, 'Only bit_rates < 32 supported.'
    bin_edges = np.linspace(-range_limit, range_limit, 2**bit_rate)
    bin_size = 2 * range_limit / (2**bit_rate - 1)
    X_q = torch.tensor(X) # creates a copy of X
    if stochastic_round:
        X_q -= torch.rand(X_q.shape) * bin_size
        interp = interp1d(bin_edges,bin_edges,kind='next') # fill_value='extrapolate'
    else:
        interp = interp1d(bin_edges,bin_edges,kind='nearest') # fill_value='extrapolate'
    X_q = torch.from_numpy(interp(X_q.numpy()))
    return X_q

def get_max_abs(X):
    return torch.max(torch.abs(X))

# TESTS
def test1():
    X = torch.tensor([-1.5,-0.5,0.5,1.5])
    X_expect = torch.tensor([-1.0,-1.0,1.0,1.0])
    Xq = clamp_and_quantize(X,bit_rate=1,range_limit=1)
    assert torch.all(torch.eq(Xq, X_expect)).item() == 1

def test2():
    X = torch.tensor([-1.5,-0.5,0.5,1.5])
    X_expect = X
    Xq = clamp_and_quantize(X,bit_rate=2)
    assert torch.all(torch.eq(Xq, X_expect)).item() == 1

def test3():
    X = torch.tensor([-1.5,-0.5,0.5,1.5])
    X_expect = X
    Xq = clamp_and_quantize(X,bit_rate=2,range_limit=2,use_midriser=True)
    assert torch.all(torch.eq(Xq, X_expect)).item() == 1
"""

def uniform_quantize(X, bit_rate, adaptive_range=False, stochastic_round=False, 
        skip_quantize=False):
    X = torch.Tensor(X)
    if adaptive_range:
        range_limit = find_optimal_range(X, bit_rate)
    else:
        range_limit = get_max_abs(X)

    return _uniform_quantize(X, bit_rate, range_limit, 
        stochastic_round=stochastic_round, skip_quantize=skip_quantize).numpy()

# Internal function.  This one expects an explicit range_limit.
def _uniform_quantize(X, bit_rate, range_limit, stochastic_round=False, 
        skip_quantize=False):
    '''
    Internal uniform quantization function (ADD MORE DESCRIPTION)
    '''
    assert range_limit >= 0, 'range_limit must be non-negative.'
    if get_max_abs(X) > range_limit:
        X_q = torch.clamp(X, min=-range_limit, max=range_limit)
    if bit_rate < 32 and range_limit != 0 and not skip_quantize:
        # affine transform to put X in [0,2**bit_rate - 1]
        X_q = (2**bit_rate - 1) * (X + range_limit) / (2 * range_limit)
        if stochastic_round:
            # each entry will round down if noise > fraction part
            X_q = torch.ceil(X_q - torch.rand(X_q.shape))
        else:
            X_q = torch.round(X_q)
        # undo affine transformation
        X_q = (X_q * 2 * range_limit) / (2**bit_rate - 1) - range_limit 
    return X_q

def find_optimal_range(X, bit_rate, tol=1e-2):
    '''
    Find the best value to use to clip the embeddings before using uniform quantization.
    '''
    f = lambda range_limit : quantize_and_compute_frob_error(
        X, bit_rate, range_limit)

    return golden_section_search(f, 0, get_max_abs(X), tol=tol)

def quantize_and_compute_frob_error(X, bit_rate, range_limit):
    '''
    Function which computes Frob error after quantizing (ADD MORE DESCRIPTION).
    '''
    X_q = _uniform_quantize(X, bit_rate, range_limit)
    return torch.norm(X - X_q)

def golden_section_search(f, x_min, x_max, tol=1e-2):
    '''
    Find argmin of f between x_min and x_max (for f uni-modal).
    
    This function uses the golden-section search algorithm.
    It always maintains a list of four points [x1,x2,x3,x4],
    which are always spaced as: [a,a+(c^2)h,a+ch,a+h].
    for c = (math.sqrt(5) - 1) / 2 = 0.618...
    The algorithm progressively reduces the size of the interval being
    considered by checking whether f(x2) < f(x3), and eliminating one of the
    endpoints accordingly; x4 is eliminated if f(x2) < f(x3), and x1 
    is eliminated otherwise.
    
    If f(a+(c^2)h) < f(a+ch), the new interval becomes
    >>> [a,a+(c^3)h,a+(c^2)h,a+ch] = [a,a+(c^2)(ch),a+c(ch),a+ch]
    (So h' = ch, a' = a)
    Otherwise, the interval becomes
    >>> [a',a'+(c^2)h',a'+ch', a'+h'], for a' = a+(c^2)h and h'=(h-(c^2)h)
    It is easy to check that a'+(c^2)h' = a + ch, and that a'+h' = a+h,
    So this interval is equal to [a+(c^2)h, a+ch, X, a+h], for X=a'+ch'

    The algorithm terminates when it has been narrowed
    down that the argmin must be in an interval of size < tol.
    '''
    #initialize points
    c = (math.sqrt(5) - 1) / 2
    x1 = x_min
    x4 = x_max
    f_x1 = f(x1)
    f_x4 = f(x4)
    x2 = x1 + (x4-x1) * c**2
    x3 = x1 + (x4-x1) * c
    f_x2 = f(x2)
    f_x3 = f(x3)
    while (x4-x1 > tol):
        assert (math.isclose(x2, x1 + (x4 - x1) * c**2) and 
                math.isclose(x3, x1 + (x4 - x1) * c))
        if f_x2 < f_x3:
            # The new points become [x1, NEW, x2, x3]
            x4,f_x4 = x3,f_x3
            x3,f_x3 = x2,f_x2
            x2 = x1 + (x4-x1) * c**2
            f_x2 = f(x2)
        else:
            # The new points become [x2, x3, NEW, x4]
            x1,f_x1 = x2,f_x2
            x2,f_x2 = x3,f_x3
            x3 = x1 + (x4-x1) * c
            f_x3 = f(x3)
        
    # Return x-value with minimum f(x) which was found.
    i = np.argmin([f_x1,f_x2,f_x3,f_x4])
    x = [x1,x2,x3,x4]
    return x[i]

def get_max_abs(X):
    return torch.max(torch.abs(X)).item()

# HERE IS ANOTHER VERSION OF EVERYTHING, CLOSER TO YOUR VERSION
'''
CORE QUANTIZER
'''
def uniform_quantize_v2(X, bit_rate, range_limit_finder, quantize_func):
    range_limit = range_limit_finder(X, bit_rate)
    X_q = X.tensor(X) # create copy
    if get_max_abs(X) > range_limit:
        torch.clamp(X_q, min=-range_limit, max=range_limit)
    if bit_rate < 32 and range_limit != 0 and quantize_func != _no_round:
        # affine transform to put X in [0,2**bit_rate - 1]
        X_q = (2**bit_rate - 1) * (X_q + range_limit) / (2 * range_limit)
        X_q = quantize_func(X,bit_rate)
        # undo affine transformation
        X_q = (X_q * 2 * range_limit) / (2**bit_rate - 1) - range_limit 
    return X_q

'''
Range solvers
'''
def _adaptive_range(X, bit_rate):
    return find_optimal_range_v2(X, bit_rate)

def _full_range(X, bit_rate):
    return torch.max(torch.abs(X))

'''
Rounding schemes
'''
def _stochastic_round(X):
    return torch.ceil(X - torch.rand(X.shape))

def _deterministic_round(X):
    return torch.round(X)

def _no_round(X):
    return X

'''
HIGH-LEVEL QUANTIZATION CALLS
'''
def full_range_deterministic(X, bit_rate):
    return uniform_quantize_v2(X, bit_rate, _full_range, _deterministic_round)
def full_range_stochastic(X, bit_rate):
    return uniform_quantize_v2(X, bit_rate, _full_range, _stochastic_round)
def adaptive_range_deterministic(X, bit_rate):
    return uniform_quantize_v2(X, bit_rate, _adaptive_range, _deterministic_round)
def adaptive_range_stochastic(X, bit_rate):
    return uniform_quantize_v2(X, bit_rate, _adaptive_range, _stochastic_round)
def adaptive_range_no_quantize(X, bit_rate):
    return uniform_quantize_v2(X, bit_rate, _adaptive_range, _no_round)

def find_optimal_range_v2(X, bit_rate, tol=1e-2):
    '''
    Find the best value to use to clip the embeddings before using uniform quantization.
    '''
    # TODO: DO WE WANT TO USE STOCHASTIC ROUNDING IN THIS SEARCH WHEN 
    # STOCHASTIC ROUNDING IS BEING USED FOR THE QUANTIZATION?
    f = lambda range_limit : quantize_and_compute_frob_error(
        X, bit_rate, range_limit, stochastic_round=False)

    return golden_section_search(f, 0, get_max_abs(X), tol=tol)

def quantize_and_compute_frob_error_v2(X, bit_rate, range_limit, stochastic_round=False):
    '''
    Function which computes Frob error after quantizing (ADD MORE DESCRIPTION).
    '''
    # This range limit finder always returns the specified range_limit
    range_limit_finder = lambda X_,bit_rate_ : range_limit
    quantize_func = _stochastic_round if stochastic_round else _deterministic_round
    X_q = uniform_quantize_v2(X, bit_rate, range_limit_finder, quantize_func)
    return torch.norm(X - X_q)