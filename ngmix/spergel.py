"""
todo:

    errors and s/n calculations.
    note everything is for round pars

"""
from __future__ import print_function

import numpy

from .fitting import LMSimple, run_leastsq, print_pars

from . import observation
from .observation import Observation, ObsList, MultiBandObsList

from .priors import LOWVAL,BIGVAL
from .gexceptions import GMixRangeError

class SpergelRunner(object):
    """
    wrapper to generate guesses and run the fitter a few times
    """
    def __init__(self, obs, lm_pars, guesser, prior=None):

        self.obs=obs
        self.lm_pars=lm_pars
        self.guesser=guesser
        self.prior=prior

    def get_fitter(self):
        return self.fitter

    def go(self, ntry=1):

        fitcls = self._get_fitter_class()

        for i in xrange(ntry):
            guess=self.guesser()
            fitter=fitcls(self.obs,
                          lm_pars=self.lm_pars,
                          prior=self.prior)

            fitter.go(guess)

            res=fitter.get_result()
            if res['flags']==0:
                break

        res['ntry'] = i+1
        self.fitter=fitter

    def _get_fitter_class(self):
        return LMSpergel

class SpergelRunnerPS(SpergelRunner):
    """
    fit the power spectrum
    """
    def _get_fitter_class(self):
        return LMSpergelPS

class SpergelExpRunner(SpergelRunner):
    """
    wrapper to generate guesses and run the fitter a few times
    """
    def _get_fitter_class(self):
        return LMSpergelExp




class LMSpergel(LMSimple):
    """
    Fit the spergel profile to the input observations

    Fitting is done in k space, with the exact PSF
    """
    def __init__(self, obs, **keys):
        self.keys=keys
        self.model_name='spergel'

        self._set_kobs(obs)
        self._init_model_images()

        self._set_fitting_pars(**keys)
        self._set_prior(**keys)

        self._set_band_pars()
        self._set_totpix()

        self._set_fdiff_size()

    def go(self, guess):
        """
        Run leastsq and set the result
        """

        guess=self._get_guess(guess)

        result = run_leastsq(self._calc_fdiff,
                             guess,
                             self.n_prior_pars,
                             k_space=True,
                             **self.lm_pars)

        result['model'] = self.model_name
        if result['flags']==0:
            result['g'] = result['pars'][2:2+2].copy()
            result['g_cov'] = result['pars_cov'][2:2+2, 2:2+2].copy()
            stat_dict=self.get_fit_stats(result['pars'])
            result.update(stat_dict)

        self._result=result

    def _calc_fdiff(self, pars, more=False):
        """

        vector with (model-data)/error.

        The npars elements contain -ln(prior)
        """

        # we cannot keep sending existing array into leastsq, don't know why
        fdiff=numpy.zeros(self.fdiff_size)

        s2n_numer=0.0
        s2n_denom=0.0
        npix = 0

        try:


            self._fill_models(pars)

            start=self._fill_priors(pars, fdiff)

            for band in xrange(self.nband):

                kobs_list=self.mb_kobs[band]
                for kobs in kobs_list:

                    meta =kobs.meta
                    ierr = meta['ierr']

                    tmp_fdiff = meta['scratch']
                    imsize = tmp_fdiff.array.size

                    # the real part
                    tmp_fdiff.array[:,:] = meta['krmult'].array[:,:]
                    tmp_fdiff -= kobs.kr

                    tmp_fdiff *= ierr

                    fdiff[start:start+imsize] = tmp_fdiff.array.ravel()

                    npix += imsize
                    start += imsize

                    # the imaginary part
                    tmp_fdiff.array[:,:] = meta['kimult'].array[:,:]
                    tmp_fdiff -= kobs.ki

                    tmp_fdiff *= ierr

                    fdiff[start:start+imsize] = tmp_fdiff.array.ravel()

                    npix += imsize
                    start += imsize

                    if more:
                        s2n_numer += (kobs.kr*meta['krmult']*kobs.weight).array.sum()
                        s2n_numer += (kobs.ki*meta['kimult']*kobs.weight).array.sum()

                        s2n_denom += (meta['krmult']**2 *kobs.weight).array.sum()
                        s2n_denom += (meta['kimult']**2 *kobs.weight).array.sum()

            n=self.n_prior_pars
            #print_pars(fdiff[0:n], front='    fdiff prior: ')
            #print_pars(fdiff[n:n+5], front='    fdiff rest: ')

        except GMixRangeError as err:
            fdiff[:] = LOWVAL
            s2n_numer=0.0
            s2n_denom=BIGVAL

        if more:

            # we need to calculate these
            #s2n_numer = 1.0
            #s2n_denom = 1.0
            return {'fdiff':fdiff,
                    's2n_numer':s2n_numer,
                    's2n_denom':s2n_denom,
                    'npix':npix}
        else:
            return fdiff

    def _fill_models(self, pars):
        """
        input pars are in linear space

        Fill the list of lists of gmix objects for the given parameters
        """

        for band,kobs_list in enumerate(self.mb_kobs):
            # pars for this band, in linear space
            band_pars=self.get_band_pars(pars, band)

            for i,kobs in enumerate(kobs_list):

                gal = self.make_model(band_pars)

                meta=kobs.meta

                kr=meta['krmodel']
                ki=meta['kimodel']

                gal.drawKImage(
                    re=kr,
                    im=ki,
                )

                scratch=meta['scratch']
                krmult=meta['krmult']
                kimult=meta['kimult']

                _complex_multiply(
                    kr.array, ki.array,
                    kobs.psf.kr.array, kobs.psf.ki.array,
                    scratch.array,
                    krmult.array, 
                    kimult.array, 
                )


    def make_model(self, pars):
        """
        make the galsim Spergel model
        """
        import galsim

        shift = pars[0:0+2]
        g1    = pars[2]
        g2    = pars[3]
        r50   = pars[4]
        nu    = pars[5]
        flux  = pars[6]

        # argh, this throws a runtime error of all things so
        # there is no way to tell what went wrong
        try:
            gal = galsim.Spergel(
                nu,
                half_light_radius=r50,
                flux=flux,
            )
        except RuntimeError as err:
            raise GMixRangeError(str(err))

        try:
            gal = gal.shear(g1=g1, g2=g2)
        except ValueError as err:
            raise GMixRangeError(str(err))

        gal = gal.shift(shift)
        return gal

    def get_band_pars(self, pars_in, band):
        """
        Get linear pars for the specified band

        input pars are [c1, c2, e1, e2, r50, nu, flux1, flux2, ....]
        """

        pars=self._band_pars

        pars[0:6] = pars_in[0:6]
        pars[6] = pars_in[6+band]
        return pars



    def _set_fitting_pars(self, **keys):
        """
        set the fit pars, in this case for the LM algorithm
        """
        lm_pars=keys.get('lm_pars',None)
        if lm_pars is None:
            lm_pars=_default_lm_pars
        self.lm_pars=lm_pars

    def _set_totpix(self):
        """
        Make sure the data are consistent.
        """

        totpix=0
        for kobs_list in self.mb_kobs:
            for kobs in kobs_list:
                totpix += kobs.kr.array.size

        self.totpix=totpix


    def _convert2kobs(self, obs):
        kobs = observation.make_kobs(obs, **self.keys)

        return kobs

    def _set_kobs(self, obs_in, **keys):
        """
        Input should be an Observation, ObsList, or MultiBandObsList
        """

        if isinstance(obs_in, (Observation, ObsList, MultiBandObsList)):
            kobs=self._convert2kobs(obs_in)
        else:
            kobs=observation.get_kmb_obs(obs_in)

        self.mb_kobs = kobs
        self.nband=len(kobs)

    def _set_prior(self, **keys):
        self.prior = keys.get('prior',None)
        if self.prior is None:
            self.n_prior_pars=0
        else:
            #                 c1  c2  e1e2  r50  nu   fluxes
            self.n_prior_pars=1 + 1 + 1   + 1  + 1  + self.nband

    def _set_fdiff_size(self):
        # we have 2*totpix, since we use both real and imaginary 
        # parts
        self.fdiff_size = self.n_prior_pars + 2*self.totpix


    def _create_models_in_kobs(self, kobs):
        ex=kobs.kr

        meta=kobs.meta
        meta['krmodel'] = ex.copy()
        meta['kimodel'] = ex.copy()

        meta['scratch'] = ex.copy()
        meta['krmult'] = ex.copy()
        meta['kimult'] = ex.copy()


    def _init_model_images(self):
        """
        add model image entries to the metadata for
        each observation

        these will get filled in
        """

        for kobs_list in self.mb_kobs:
            for kobs in kobs_list:
                meta=kobs.meta

                weight = kobs.weight
                ierr = weight.copy()
                ierr.setZero()

                w=numpy.where(weight.array > 0)
                if w[0].size > 0:
                    ierr.array[w] = numpy.sqrt(weight.array[w])

                meta['ierr'] = ierr
                self._create_models_in_kobs(kobs)


    def _check_guess(self, guess):
        """
        check the guess by making a model and checking for an
        exception
        """

        guess=numpy.array(guess,dtype='f8',copy=False)
        if guess.size != self.npars:
            raise ValueError("expected %d entries in the "
                             "guess, but got %d" % (self.npars,guess.size))

        for band in xrange(self.nband):
            band_pars = self.get_band_pars(guess, band)
            gal = self.make_model(band_pars)

        return guess

    def _get_guess(self, guess):
        """
        make sure the guess has the right size and meets the model
        restrictions
        """

        guess=self._check_guess(guess)
        return guess


    def _set_npars(self):
        """
        nband should be set in set_lists, called before this
        """
        self.npars=6 + self.nband

    def _set_band_pars(self):
        """
        this is the array we fill with pars for a specific band
        """
        self._set_npars()
        self._band_pars=numpy.zeros(7)

    def get_fit_stats(self, pars):
        """
        Get some fit statistics for the input pars.
        """
        npars=self.npars

        res=self._calc_fdiff(pars, more=True)

        if res['s2n_denom'] > 0:
            s2n_w=res['s2n_numer']/numpy.sqrt(res['s2n_denom'])
        else:
            s2n_w=0.0

        s2n_r_sum = self._calc_s2n_r_sum(pars)
        if s2n_r_sum > 0.0:
            s2n_r = numpy.sqrt(s2n_r_sum)
        else:
            s2n_r = 0.0

        res['s2n_w']   = s2n_w
        res['s2n_r']   = s2n_r

        return res

    def _calc_s2n_r_sum(self, pars):
        """
        we already have the round r50, so just create the
        models and don't shear them
        """

        s2n_sum=0.0
        for band,kobs_list in enumerate(self.mb_kobs):
            # pars for this band, in linear space
            band_pars=self.get_band_pars(pars, band)

            for i,kobs in enumerate(kobs_list):

                round_pars=band_pars.copy()
                round_pars[2:2+2] = 0.0
                gal = self.make_model(round_pars)

                meta=kobs.meta

                kr=meta['krmodel']
                ki=meta['kimodel']

                gal.drawKImage(
                    re=kr,
                    im=ki,
                )

                scratch=meta['scratch']
                krmult=meta['krmult']
                kimult=meta['kimult']

                _complex_multiply(
                    kr.array, ki.array,
                    kobs.psf.kr.array, kobs.psf.ki.array,
                    scratch.array,
                    krmult.array, 
                    kimult.array, 
                )

                s2n_sum += (krmult**2*kobs.weight).array.sum()
                s2n_sum += (kimult**2*kobs.weight).array.sum()

        return s2n_sum


class LMSpergelExp(LMSpergel):
    """
    Fit the spergel profile with nu=0.5 to the input observations.
    This is identical to an exponential.

    Fitting is done in k space, with the exact PSF
    """

    def __init__(self, *args, **keys):
        super(LMSpergelExp,self).__init__(*args, **keys)
        self.model_name='exp'
        self.nu=0.5

    def get_band_pars(self, pars_in, band):
        """
        Get linear pars for the specified band

        input pars are [c1, c2, e1, e2, r50, flux1, flux2, ....]
        """

        pars=self._band_pars

        # copy c1,c2,e1,e2,r50
        pars[0:5] = pars_in[0:5]
        pars[5]   = self.nu
        pars[6]   = pars_in[5+band]
        return pars

    def _set_npars(self):
        """
        nband should be set in set_lists, called before this
        """
        self.npars=5 + self.nband


    def _set_prior(self, **keys):
        self.prior = keys.get('prior',None)
        if self.prior is None:
            self.n_prior_pars=0
        else:
            #                 c1  c2  e1e2  r50  fluxes
            self.n_prior_pars=1 + 1 + 1   + 1  + self.nband


class LMSpergelPS(LMSpergel):
    """
    Fit the spergel profile to the input observations.
    Fitting is to the power spectrum in k space, with the exact PSF

    for fitting with phase information, use LMSpergel
    """

    def _calc_fdiff(self, pars, more=False):
        """

        vector with (model-data)/error.

        The npars elements contain -ln(prior)
        """

        # we cannot keep sending existing array into leastsq, don't know why
        fdiff=numpy.zeros(self.fdiff_size)

        s2n_numer=0.0
        s2n_denom=0.0
        npix = 0

        try:


            self._fill_models(pars)

            start=self._fill_priors(pars, fdiff)

            for band in xrange(self.nband):

                kobs_list=self.mb_kobs[band]
                for kobs in kobs_list:

                    meta = kobs.meta
                    ierr = meta['ierr']
                    imsize = ierr.array.size

                    tmp_fdiff = meta['scratch']

                    tmp_fdiff.array[:,:] = meta['psmodel'].array[:,:]

                    """
                    import images
                    images.multiview(meta['ps'].array,title='ps')
                    images.multiview(tmp_fdiff.array,title='model ps')
                    stop
                    """

                    # need to make sure noise ps subtracted too
                    tmp_fdiff -= meta['ps']
                    tmp_fdiff *= ierr

                    fdiff[start:start+imsize] = tmp_fdiff.array.ravel()

                    npix += imsize
                    start += imsize


        except GMixRangeError as err:
            fdiff[:] = LOWVAL
            s2n_numer=0.0
            s2n_denom=BIGVAL

        if more:
            # we need to calculate these
            s2n_numer = 1.0
            s2n_denom = 1.0
            return {'fdiff':fdiff,
                    's2n_numer':s2n_numer,
                    's2n_denom':s2n_denom,
                    'npix':npix}
        else:
            return fdiff

    def _fill_models(self, pars):
        """
        input pars are in linear space

        Fill the list of lists of gmix objects for the given parameters
        """

        for band,kobs_list in enumerate(self.mb_kobs):
            # pars for this band, in linear space
            band_pars=self.get_band_pars(pars, band)

            for i,kobs in enumerate(kobs_list):

                meta=kobs.meta

                gal = self.make_model(band_pars)

                # fill the existing image
                kr=meta['krmodel']
                ki=meta['kimodel']

                gal.drawKImage(
                    re=kr,
                    im=ki,
                )

                scratch=meta['scratch']
                krmult=meta['krmult']
                kimult=meta['kimult']
                psmodel=meta['psmodel']

                _complex_multiply(
                    kr.array, ki.array,
                    kobs.psf.kr.array, kobs.psf.ki.array,
                    scratch.array,
                    krmult.array, 
                    kimult.array, 
                )

                scratch=meta['scratch']

                numpy.square(krmult.array, psmodel.array)
                numpy.square(kimult.array, scratch.array)

                psmodel += scratch


    def make_model(self, pars):
        """
        make the galsim Spergel model
        """
        import galsim

        g1    = pars[0]
        g2    = pars[1]
        r50   = pars[2]
        nu    = pars[3]
        flux  = pars[4]

        # argh, this throws a runtime error of all things so
        # there is no way to tell what went wrong
        try:
            gal = galsim.Spergel(
                nu,
                half_light_radius=r50,
                flux=flux,
            )
        except RuntimeError as err:
            raise GMixRangeError(str(err))

        try:
            gal = gal.shear(g1=g1, g2=g2)
        except ValueError as err:
            raise GMixRangeError(str(err))

        return gal


    def _create_models_in_kobs(self, kobs):
        super(LMSpergelPS,self)._create_models_in_kobs(kobs)
        kobs.meta['psmodel'] = kobs.meta['krmodel'].copy()

    def _convert2kobs(self, obs):
        kobs = observation.make_kobs(obs, ps=True, **self.keys)
        return kobs

    def _set_prior(self, **keys):
        self.prior = keys.get('prior',None)
        if self.prior is None:
            self.n_prior_pars=0
        else:
            #                 e1e2  r50  nu   fluxes
            self.n_prior_pars=1   + 1  + 1  + self.nband

    def _set_fdiff_size(self):
        self.fdiff_size = self.n_prior_pars + self.totpix

    def _set_npars(self):
        """
        nband should be set in set_lists, called before this
        """
        self.npars=4 + self.nband

    def get_band_pars(self, pars_in, band):
        """
        Get linear pars for the specified band

        input pars are [e1, e2, r50, nu, flux1, flux2, ....]
        """

        pars=self._band_pars

        pars[0:4] = pars_in[0:4]
        pars[4] = pars_in[4+band]
        return pars

def _complex_multiply(a, b, c, d, scratch, real_res, imag_res):
    """
    (a + i *b) * (c + i *d)
    =
    (ac-bd) + i* (ad + bc)
    """

    numpy.multiply(a, c, real_res)
    numpy.multiply(b, d, scratch)

    real_res -= scratch

    numpy.multiply(a, d, imag_res)
    numpy.multiply(b, c, scratch)

    imag_res += scratch


